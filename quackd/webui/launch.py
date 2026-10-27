"""`quackd ui`: where the page listens, the token it asks for, and the way to it from a phone.

**Loopback by default, and a token always.** The page drives robots, and a yes clicked in it
counts as a person's (ADR-0069), which holds only while the page is reachable by its own user
alone. So it listens on 127.0.0.1 unless `--bind` says otherwise, and every session must bring its
token, on loopback too: another account on the same computer reaches 127.0.0.1, and so can a web
page in your own browser that rebinds a name of its own to it. The token is made the first time,
kept in a file only its owner may read, and the same at every start after, so a phone's home-screen
shortcut keeps working; `--new-token` replaces it. The browser this computer opens is handed it.

**Streamlit is told, not left to its defaults or a project's.** Every setting that decides who
reaches the page is passed on Streamlit's command line, which outranks a `.streamlit/config.toml`
or a variable a cloned project could bring: the address, CORS and XSRF protection on, no base
path. The cookie secret, which Streamlit refuses on its command line, is this start's own, set in
its environment, which outranks a config file too. Its usage statistics are off, and so is its
watch on the page's files, which never change under a running page.

**The way to it from a phone** is printed with the token in it, for every address this computer
has on its networks, with a QR code of the first drawn in the terminal, and the page shows the same
code in its sidebar, under "Open on your phone", so a session begun at a desk can be picked up on
the phone.

Streamlit is imported only by `serve`, after the CLI has checked it is installed.
"""

from __future__ import annotations

import contextlib
import io
import os
import secrets
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LOOPBACK = ("127.0.0.1", "::1", "localhost")
EVERY_NETWORK = ("0.0.0.0", "::", "")
TOKEN_FILE = "ui.token"
QR_FILE = "ui-qr.png"
DEFAULT_PORT = 8501


@dataclass(frozen=True)
class Config:
    """What the page knows of how it was started, read by the page from this module."""

    folder: Path
    """The working folder: where runs start, where their `.env` and `runs/` are."""
    bind: str
    port: int
    token: str | None
    """The token every session must bring. `quackd ui` always sets one; None is for a page
    built without the launcher, in tests."""
    urls: tuple[str, ...]
    """The addresses the page is reached at, each with its token, the first the one to scan."""
    home: Path | None = None
    """Where the token and the saved setup are kept: the quackd home the shell names, never one
    a `.env` names."""


CONFIG: Config | None = None
"""Set by `serve` before Streamlit starts the page, in the same process the page runs in."""


def is_loopback(bind: str) -> bool:
    return bind.strip().lower() in LOOPBACK


def local_host(bind: str) -> str:
    """Where this computer itself reaches the page: loopback for a loopback or an every-network
    bind, and the one address a bind names otherwise, which is all a page bound to it answers."""
    address = bind.strip()
    if address.lower() == "::1":
        return "::1"
    if is_loopback(address) or address in EVERY_NETWORK:
        return "127.0.0.1"
    return address


def _url_host(address: str) -> str:
    """`address` as it goes in a URL: an IPv6 address in brackets."""
    return f"[{address}]" if ":" in address else address


def _write_private(path: Path, data: bytes) -> None:
    """`data` as `path`'s whole content, in a file only its owner may read from the moment it
    exists: made with that mode, written, then renamed over the old one, as `registry.write_json`
    keeps a token. A no-op for the mode on Windows, where a file takes its profile's permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with contextlib.suppress(FileNotFoundError):
        tmp.unlink()
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def token(directory: Path, *, renew: bool = False) -> str:
    """The page's token, made once and kept in `directory`, readable by its owner alone."""
    path = directory / TOKEN_FILE
    if not renew and path.is_file():
        kept = path.read_text(encoding="utf-8").strip()
        if kept:
            return kept
    made = secrets.token_urlsafe(18)
    _write_private(path, (made + "\n").encode("utf-8"))
    return made


def lan_addresses() -> list[str]:
    """This computer's IPv4 addresses on its networks, the one its default route uses first.

    The first is found by pointing a UDP socket at a documentation address, which sends nothing
    and asks the system which address it would send from."""
    found: list[str] = []
    with contextlib.suppress(OSError), socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect(("192.0.2.1", 9))
        found.append(probe.getsockname()[0])
    with contextlib.suppress(OSError):
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(str(info[4][0]))
    out: list[str] = []
    for address in found:
        if address not in out and not address.startswith(("127.", "0.")):
            out.append(address)
    return out


def urls(bind: str, port: int, token_value: str | None) -> list[str]:
    """Every address the page can be reached at, each with its token."""
    tail = f"/?token={token_value}" if token_value else ""
    if bind.strip() in EVERY_NETWORK:
        hosts = lan_addresses() or ["127.0.0.1"]
    else:
        hosts = [local_host(bind)]
    return [f"http://{_url_host(host)}:{port}{tail}" for host in hosts]


def qr_image(text: str, *, module_px: int = 6) -> Any:
    """`text` as a QR code picture, black on white, the quiet zone a scanner needs included."""
    from PIL import Image

    from quackd.passport_qr import qr_modules

    rows = qr_modules(text)
    picture = Image.new("1", (len(rows[0]), len(rows)), 1)
    for y, row in enumerate(rows):
        for x, dark in enumerate(row):
            if dark:
                picture.putpixel((x, y), 0)
    return picture.resize(
        (picture.width * module_px, picture.height * module_px), Image.Resampling.NEAREST
    )


def qr_lines(text: str) -> list[str]:
    """`text` as a QR code in half blocks, two rows of modules to a line of text, light modules
    drawn and dark ones left as the terminal's own dark background, which is how a phone reads
    a code off a dark terminal."""
    from quackd.passport_qr import qr_modules

    rows = qr_modules(text)
    if len(rows) % 2:
        rows.append([False] * len(rows[0]))
    glyph = {(True, True): "█", (True, False): "▀", (False, True): "▄", (False, False): " "}
    return [
        "".join(glyph[(not top, not bottom)] for top, bottom in zip(upper, lower, strict=True))
        for upper, lower in zip(rows[::2], rows[1::2], strict=True)
    ]


def write_qr_png(text: str, path: Path) -> None:
    """`text` as a QR code PNG at `path`, private as the token it carries is."""
    picture = io.BytesIO()
    qr_image(text).save(picture, format="PNG")
    _write_private(path, picture.getvalue())


def port_taken(bind: str, port: int) -> bool:
    """Whether something already listens where the page would, or answers at the address on this
    computer the page prints and opens. Asked before Streamlit starts, because Streamlit says a
    port is taken only after the page has announced an address another program answers."""
    with (
        contextlib.suppress(OSError),
        socket.create_connection((local_host(bind), port), timeout=0.5),
    ):
        return True
    address = bind.strip() or "0.0.0.0"
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as probe:
        if sys.platform != "win32":
            # as Streamlit's own server binds on Linux and macOS, so a port a page released a
            # moment ago, still in TIME_WAIT, counts as free; on Windows the same option would
            # let a bind share a port another program holds
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((address, port))
        except OSError:
            return True
    return False


def can_draw(stream: object) -> bool:
    """Whether `stream` can carry the half blocks: a UTF-8 one can, a cp1252 console cannot."""
    encoding = str(getattr(stream, "encoding", "") or "").lower().replace("-", "")
    return encoding.startswith("utf")


COOKIE_SECRET_ENV = "STREAMLIT_SERVER_COOKIE_SECRET"
"""Where the cookie secret goes: Streamlit refuses it as a command-line flag, and its environment
still outranks a config file. Kept out of the runs the page starts (`jobs._child_environment`)."""


def streamlit_flags(bind: str, port: int) -> list[str]:
    """How Streamlit is started under `quackd ui`. Command-line settings outrank a config file
    and the environment, so every one that decides who reaches the page is here but the cookie
    secret, which Streamlit takes from its environment alone (`COOKIE_SECRET_ENV`)."""
    return [
        "--server.address",
        bind,
        "--server.port",
        str(port),
        "--server.enableCORS",
        "true",
        "--server.enableXsrfProtection",
        "true",
        "--server.baseUrlPath",
        "",
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
        "--server.fileWatcherType",
        "none",
        "--server.runOnSave",
        "false",
        "--client.toolbarMode",
        "minimal",
        "--global.developmentMode",
        "false",
    ]


def _when_up(url: str, then: Callable[[], None], within_s: float = 60.0) -> None:
    """Call `then` once the page answers at `url`, from a thread of its own."""

    def wait() -> None:
        deadline = time.monotonic() + within_s
        while time.monotonic() < deadline:
            with (
                contextlib.suppress(OSError),
                urllib.request.urlopen(f"{url}/_stcore/health", timeout=2) as answer,
            ):
                if answer.status == 200:
                    then()
                    return
            time.sleep(0.25)

    threading.Thread(target=wait, daemon=True, name="ui-up").start()


def serve(
    *,
    folder: Path,
    bind: str,
    port: int,
    browser: bool,
    token_dir: Path,
    new_token: bool,
    say: Callable[[str], None],
) -> None:
    """Start the page and block until it stops."""
    global CONFIG
    token_value = token(token_dir, renew=new_token)
    reach = urls(bind, port, token_value)
    CONFIG = Config(
        folder=folder, bind=bind, port=port, token=token_value, urls=tuple(reach), home=token_dir
    )
    here = f"http://{_url_host(local_host(bind))}:{port}"

    def announce() -> None:
        say(f"quackd ui: {reach[0]}")
        for other in reach[1:]:
            say(f"           {other}")
        if not is_loopback(bind):
            say(
                "Anyone who can reach this address and has the token can drive your robots. "
                "Keep the address to yourself."
            )
            if can_draw(sys.stdout):
                for line in qr_lines(reach[0]):
                    say(line)
            else:
                png = token_dir / QR_FILE
                write_qr_png(reach[0], png)
                say(f"QR code for your phone: {png}")
        say("Stop in the page is not an emergency stop. Ctrl-C here stops the page and its runs.")
        if browser:
            webbrowser.open(f"{here}/?token={token_value}")

    _when_up(here, announce)
    from streamlit.web import cli as streamlit_cli

    app = Path(__file__).with_name("app.py")
    os.environ[COOKIE_SECRET_ENV] = secrets.token_hex(32)  # this start's own, whatever was set
    sys.argv = ["streamlit", "run", str(app), *streamlit_flags(bind, port)]
    streamlit_cli.main()
