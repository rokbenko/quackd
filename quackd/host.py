"""The laptop's half of the Jetson host daemon's protocol: a board quackd uses and never runs on.

`--host jetson.local` names a machine, not a body; `--robot` still names the body. quackd stays
on the laptop and reaches the board over plain HTTP, through the one small daemon it ships for
it, `bridge/jetson/quackd_jetson_hostd.py`, the way it ships a camera daemon for the Open Duck
Mini's Pi. What the daemon offers is the board's own health, a camera if one is plugged in, and
a YOLO detector on the board's GPU. This module is the client for all of it and the only place
on this side that spells the wire protocol. The daemon spells the same constants, and a change
to either end is a change to both.

It is synchronous on purpose. `doctor` and the CLI call it directly, and the two consumers
inside a run (the host camera and the host detector) call it through `asyncio.to_thread`, the
way the Open Duck bridge fetches its frames, so a slow board costs one bounded wait and never
stalls the event loop that keeps a body's keepalives flowing. Every call has a timeout sized to
what it does, so a daemon that died costs one window and never a hang. Looking up the board's
name is outside every one of them: the system resolver does it on its own clock, before there is
a socket to time out, so a name that stops resolving can cost more than the window, on every call,
since each one looks it up again. An address given to `--host` is never looked up.

It imports the standard library and PIL and nothing else, because `doctor` and the CLI import
it on every invocation and neither should pay more than that to learn a board is not there.

The token rides in one header and nowhere else. Not in a URL, because URLs are what proxies,
access logs and pasted error messages keep; not in any error this module raises, its message
or its payload; not in any reply it hands back, where a daemon that echoed it would put it on
a screen or in `doctor --json`; not in a repr. Proxies are bypassed and redirects are refused
for the same reason: either one would hand the header to a machine that is not the board.

Nothing in this module has been run against a Jetson by this project. Its tests drive it against
a fake of the protocol on loopback (`tests/fake_jetson_hostd.py`), which proves quackd reads the
protocol as written and says nothing about a board.
"""

from __future__ import annotations

import dataclasses
import http.client
import io
import ipaddress
import json
import math
import os
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeGuard, TypeVar

from PIL import Image

#: The wire contract. The daemon answers `/hello` with both, and a mismatch is refused.
PROTOCOL = "quackd-jetson-hostd"
PROTOCOL_VERSION = 1
DEFAULT_PORT = 9874
#: Where `--host` is read from when neither the flag nor a registered robot names a board.
HOST_ENV = "QUACKD_HOST"
#: Where `--host-token` is read from when neither the flag nor a registered robot gives one.
#: `HostClient` itself reads no environment: `resolve_host` settles flag, registry and
#: environment once, for every command alike.
TOKEN_ENV = "QUACKD_HOST_TOKEN"
TOKEN_HEADER = "X-Quackd-Token"
#: The refusal for a typed `--host-token` that no board takes, in `robot add`'s words.
STRAY_TOKEN = (
    "--host-token needs a board, and none is named by --host, the robot's stored host or "
    f"{HOST_ENV}: give --host too, or drop --host-token"
)

HELLO_TIMEOUT_S = 2.0
"""`/hello` and `/healthz` answer from a dictionary the daemon already holds, so anything slower
than this is a board that is not answering rather than one that is busy."""
SNAPSHOT_TIMEOUT_S = 1.0
"""The Open Duck bridge's `CAMERA_TIMEOUT_S`, for its reason: a verb steering on these frames
sends its next command after the fetch returns, and a long stall is a body moving blind."""
DETECT_TIMEOUT_S = 2.0
"""One JPEG up and one forward pass of a small model on the board. Generous, because the first
call after a quiet spell can meet a GPU at its lowest clock, and still short enough that a verb
steering on the answer waits one window at most instead of steering on an old one."""
BOARD_TIMEOUT_S = 8.0
"""The daemon forks `nvpmodel -q` and waits for one line of `tegrastats`, three seconds each at
most, so this is both of those and some slack for the reply."""
STALE_AFTER_S = 2.0
"""A frame older than this is not steering material. The daemon expires its own frames sooner
(`max(1.5, 4 / fps)`); this is the client's backstop for one that did not, copied from the Open
Duck bridge's `CAMERA_STALE_AFTER_S`."""

MAX_JPEG_BYTES = 8 * 1024 * 1024
"""The largest body `POST /detect` accepts. Checked here too, so an oversized frame is refused
before eight megabytes cross a robot's Wi-Fi only to be turned away."""
MAX_REPLY_BYTES = 16 * 1024 * 1024
"""No reply the protocol defines comes near this: a board dump is five files of at most 64 KB
and two lines. Anything bigger is not the daemon, and reading it whole is the laptop's memory."""

SNAPSHOT_PATH = "/snapshot.jpg"
_READ_CHUNK = 64 * 1024
_T = TypeVar("_T")
_HOST_EXAMPLE = f"jetson.local or jetson.local:{DEFAULT_PORT}"
_DAEMON_HINT = (
    f"start bridge/jetson/quackd_jetson_hostd.py on the board, or point --host at the port it "
    f"listens on (the default is {DEFAULT_PORT})"
)


class HostError(RuntimeError):
    """Anything that went wrong reaching the board, as one sentence that names it.

    One type, so the consumers inside a run catch exactly this and let a programming error
    through. `status` is the HTTP status when the daemon answered at all, which is how `doctor`
    tells "nothing is listening" from "something is listening and said no", and `payload` is the
    JSON object that came with a refusal, when there was one. `unresolved` is True when the
    board's name found no address, so nothing else at that name can be reached either: `doctor`
    then asks no LLM server there, since each would wait on the same failed lookup."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        payload: dict[str, Any] | None = None,
        unresolved: bool = False,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.payload = payload
        self.unresolved = unresolved


# ── the address ─────────────────────────────────────────────────────────────────────────


def parse_host(text: str) -> tuple[str, int]:
    """`--host`'s value as (host, port): the machine, and the daemon's port on it.

    Accepted: `jetson.local`, `jetson.local:9874`, `192.168.1.5`, `192.168.1.5:9000`,
    `[::1]:9874`, `[::1]`, and a bare IPv6 literal such as `fe80::1`, which can carry no port
    because its own colons would swallow one (bracket it to add a port). The host comes back
    without brackets, and the port defaults to `DEFAULT_PORT`.

    Refused, each with the sentence that says what to type instead: nothing at all; a URL,
    because `--host` names a machine and an LLM server's URL is `--base-url`'s job; anything
    with an `@`, because a token written into a machine's name ends up in shell history, in
    logs and in every message that quotes the name, which is why it has a flag and an
    environment variable of its own; a port that is not a whole number from 1 to 65535; and a
    path or a query, which a machine does not have. The refusals that could be looking at a
    secret never quote the text back."""
    value = (text or "").strip()
    if not value:
        raise ValueError(f"--host is empty: name the board, as {_HOST_EXAMPLE}")
    if "@" in value:
        raise ValueError(
            "--host names a machine and never carries a token: pass the token with "
            f"--host-token or set {TOKEN_ENV}"
        )
    if "://" in value:
        raise ValueError(
            f"--host takes a machine, not a URL: give {_HOST_EXAMPLE}. An LLM server's URL "
            "goes in --base-url"
        )
    if any(c in value for c in "/?#"):
        raise ValueError(f"--host takes a machine with no path or query: give {_HOST_EXAMPLE}")
    if any(c.isspace() or not c.isprintable() for c in value):
        raise ValueError("--host has a space or a control character in it, and no machine does")
    if value.startswith("["):
        inside, bracket, rest = value[1:].partition("]")
        if not bracket or not _is_ipv6(inside):
            raise ValueError(
                f"--host {value!r}: brackets hold an IPv6 address, as [::1] or [::1]:{DEFAULT_PORT}"
            )
        if not rest:
            return inside, DEFAULT_PORT
        if not rest.startswith(":"):
            raise ValueError(
                f"--host {value!r}: after the brackets comes a colon and a port, as "
                f"[::1]:{DEFAULT_PORT}"
            )
        return inside, _port(rest[1:])
    if value.count(":") > 1:
        # Two colons or more is an IPv6 literal, whose own colons make a trailing port
        # ambiguous: `::1:9874` is itself a valid address. RFC 3986's answer is brackets.
        if not _is_ipv6(value):
            raise ValueError(
                f"--host {value!r} is neither a machine's name nor an IPv6 address. An IPv6 "
                f"address with a port goes in brackets, as [::1]:{DEFAULT_PORT}"
            )
        return value, DEFAULT_PORT
    name, colon, port = value.partition(":")
    if not name:
        raise ValueError(f"--host {value!r} has a port and no machine: give {_HOST_EXAMPLE}")
    return name, (_port(port) if colon else DEFAULT_PORT)


def host_of(text: str) -> str:
    """The bare machine in a `--host` value, IPv6 unbracketed: what a preset's `localhost` is
    replaced with when the LLM server is on the board too."""
    return parse_host(text)[0]


def netloc(host: str, port: int) -> str:
    """`host:port` as a URL spells it, which puts an IPv6 literal in brackets."""
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


def base_url(text: str) -> str:
    """`http://HOST:PORT` for a `--host` value, IPv6 bracketed."""
    return f"http://{netloc(*parse_host(text))}"


@dataclass(frozen=True)
class HostChoice:
    """Which board a command uses, which place named it, and the token that goes with it.

    `host` is the value as it was given, stripped and checked by `parse_host`, or None when
    nothing named a board. It is kept as typed rather than as `host:port`, so a header or a
    refusal can quote back what the reader wrote. `source` is where it came from: `--host`,
    `robot NAME (robots.json)` or `QUACKD_HOST`.

    The token is left out of the repr, because an object like this ends up in a traceback
    sooner or later and the token must not end up there with it. `token_source` is where the
    token came from, in the same words (`--host-token`, the robot's entry, `QUACKD_HOST_TOKEN`),
    and `robot` is the registered robot whose entry was read, so a refusal can say how to change
    the token the run actually sends.

    Both are here because the token's ladder is not the host's. A robot's stored token outranks
    `QUACKD_HOST_TOKEN`, so "set QUACKD_HOST_TOKEN" is advice the run never reads for a robot
    that stores one, and `quackd doctor --host` alone carries no stored token at all."""

    host: str | None = None
    source: str | None = None
    token: str | None = dataclasses.field(default=None, repr=False)
    token_source: str | None = None
    robot: str | None = None

    @property
    def named(self) -> str:
        """The board as the reader named it, so a refusal points at the line they wrote: the
        flag, the robot's entry in robots.json, or the environment."""
        if self.source == "--host":
            return f"--host {self.host}"
        return f"the host {self.host} from {self.source}"

    @property
    def token_fix(self) -> str:
        """What to do when the daemon refuses this token, for the place it came from."""
        if self.token_source == "--host-token":
            return "pass the token the daemon was started with as --host-token"
        if self.token_source == TOKEN_ENV:
            return f"set {TOKEN_ENV} to the token the daemon was started with, or pass --host-token"
        if self.token_source is not None:
            return (
                f"quackd robot edit {self.robot or 'NAME'} --host-token TOKEN stores the one the "
                "daemon was started with, or pass --host-token for one run"
            )
        return f"pass --host-token or set {TOKEN_ENV} to the token the daemon was started with"

    @property
    def token_stored(self) -> bool:
        """Whether the token is the one the robot's entry in robots.json keeps. A command that
        asks the same board again has to name the robot then, since no flag carries it."""
        return self.token_source not in (None, "--host-token", TOKEN_ENV)

    @property
    def explicit(self) -> str | None:
        """The host when a person named it for this command or for this robot, else None.

        This is the value `make_provider(host=...)` is handed, and it moves a local preset's
        address above `QUACKD_BASE_URL`. A host from the environment is left out, because the
        local provider reads it for itself at the bottom of its own ladder, below
        `QUACKD_BASE_URL` and `OPENAI_BASE_URL`: a `.env` line naming the board you usually
        use must not outrank a `.env` line naming an LLM server's exact address, while a
        board typed for this run, or registered with this robot, is a decision about this run
        and does."""
        return self.host if self.source != HOST_ENV else None


def _given(value: str | None) -> str | None:
    """A setting's value, or None when it is absent or blank. `QUACKD_HOST=` in a `.env` is
    how a shell says unset, and reading it as a machine named "" would refuse every run."""
    text = (value or "").strip()
    return text or None


def resolve_host(
    flag: str | None,
    stored: str | None = None,
    *,
    token: str | None = None,
    stored_token: str | None = None,
    robot: str | None = None,
) -> HostChoice:
    """Which board this command uses, from the three places one can be named.

    The order is the one every quackd setting uses (`resolve_llm` spells it for the pilot):
    `--host` beats the host a registered robot remembers, which beats `QUACKD_HOST`, which
    beats no board at all. The robot beats the environment because it was registered by a
    person who meant it, and a variable in a shell was not necessarily meant for this robot.

    The token has a ladder of its own: `--host-token`, else the robot's stored `host_token`,
    else `QUACKD_HOST_TOKEN`. Its own rather than wherever the host came from, because
    `--host 127.0.0.1` through an ssh tunnel reaches the very board that was registered and
    still wants that board's token, which is the rule `adapter_kwargs` already follows for
    `--address` and `--token`. But the robot's token is its own board's, so it rides only when
    the robot stores a board. A host from `QUACKD_HOST` means the robot stores none, and the
    board it names is the environment's, whose token is `QUACKD_HOST_TOKEN`: sending the robot's
    token there would hand one board's credential to another and still be refused. With no
    board there is no token. `QUACKD_HOST_TOKEN` with nowhere to go is dropped rather than
    refused, since the variable in a `.env` must not make every run that names no host fail.
    A typed `--host-token` with nowhere to go is a ValueError, as `robot add` refuses one
    without `--host`: whoever typed it meant a board, and a run that quietly used none would
    look like one that had.

    Blank counts as absent at every level. A host that is not one raises ValueError in
    `parse_host`'s words, with the place it came from in front when that was not the flag,
    because every one of those sentences says `--host` and a reader who typed nothing of the
    kind would go looking on the wrong line. A token an HTTP header cannot carry raises
    ValueError too, the same way and without quoting it. Both can come from robots.json, which
    reads leniently so that one bad line cannot stop every registry command, so this is where
    a bad stored one is refused: on the run that would have used it."""
    where = f"robot {robot} (robots.json)" if robot else "robots.json"
    for value, source in ((flag, "--host"), (stored, where), (os.environ.get(HOST_ENV), HOST_ENV)):
        text = _given(value)
        if text is None:
            continue
        try:
            parse_host(text)
        except ValueError as e:
            raise ValueError(str(e) if source == "--host" else f"{source}: {e}") from e
        tokens = (
            (token, "--host-token"),
            (stored_token if _given(stored) else None, where),
            (os.environ.get(TOKEN_ENV), TOKEN_ENV),
        )
        chosen, came_from = next(((t, s) for t, s in tokens if _given(t)), (None, None))
        try:
            cleaned = clean_token(chosen)
        except ValueError as e:
            raise ValueError(str(e) if came_from == "--host-token" else f"{came_from}: {e}") from e
        return HostChoice(text, source, cleaned, came_from, robot)
    if _given(token) is not None:
        raise ValueError(STRAY_TOKEN)
    return HostChoice()


def reach_host(choice: HostChoice) -> tuple[HostClient, HostHello] | None:
    """The board a run or a server uses, asked what it is, or None when nothing named one.

    `run` and `serve-mcp` both call this before anything connects to the body, and both
    refuse when it raises `HostError`: a `--host` whose daemon does not answer is a run that
    was promised a camera or a detector it will not have, and saying so while nothing is
    powered is the time to say it. Someone who wants only the board's LLM server has
    `--base-url` for that, which asks nothing of the daemon."""
    if choice.host is None:
        return None
    client = HostClient(choice.host, token=choice.token, token_fix=choice.token_fix)
    return client, client.hello()


def unreached(choice: HostChoice, error: HostError) -> str:
    """What `run` and `serve-mcp` refuse with when the board's `/hello` failed: the board as the
    reader named it (the flag, the robot's entry, or the environment), and what happened. A
    daemon that sent back a status answered, and is said to have, because "did not answer" in
    front of a refused token sends the reader off to check the network."""
    how = "did not answer" if error.status is None else f"answered with HTTP {error.status}"
    return f"{choice.named} {how}: {error}"


def _port(text: str) -> int:
    # isascii as well as isdigit: `int()` accepts Arabic-Indic and full-width digits, and a port
    # that reads as one number on the screen and another on the wire helps nobody
    if text.isascii() and text.isdigit():
        port = int(text)
        if 1 <= port <= 65535:
            return port
    raise ValueError(f"the port in --host must be a whole number from 1 to 65535, not {text!r}")


def _is_ipv6(text: str) -> bool:
    try:
        ipaddress.IPv6Address(text)
    except ValueError:
        return False
    return True


def clean_token(token: str | None) -> str | None:
    """The token as it goes on the wire, or None for no token.

    Stripped, because the usual token file is `openssl rand -hex 32 | tee`, which ends in a
    newline, and HTTP drops the whitespace around a header value anyway. Checked for characters
    a header cannot carry, because `http.client` refuses such a header with a ValueError that
    quotes its value, and that value would be the token."""
    cleaned = (token or "").strip()
    if not cleaned:
        return None
    if not (cleaned.isascii() and cleaned.isprintable()):
        raise ValueError(
            "the host token has a character an HTTP header cannot carry; a token made with "
            "`openssl rand -hex 32` has none"
        )
    return cleaned


def confidence_floor(conf: float) -> float:
    """`conf` as a confidence floor the daemon takes, or a ValueError, which is a caller's
    mistake and not the board's.

    Above 0 and at most 1: the daemon's own rule for `?conf=` and `--conf`. A floor of 0 would
    be sent and refused with a 400, and that refusal is a HostError, the type every consumer
    reads as the board failing, so `HostDetector` would take a caller's bug for an outage and
    run blind. NaN is refused, and so is a bool, which Python would otherwise read as 0 or 1."""
    if isinstance(conf, bool) or not 0.0 < float(conf) <= 1.0:
        raise ValueError(f"conf is a confidence floor above 0 and at most 1, not {conf!r}")
    return float(conf)


# ── what the daemon says ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class HostHello:
    """What the daemon said about itself on `/hello`, already checked for shape.

    The dictionaries are the daemon's own, kept whole so `doctor --json` can show everything it
    said; the properties are the questions the rest of quackd asks of them."""

    protocol: str
    protocol_version: int
    daemon_version: str
    hostname: str
    python: str
    capabilities: dict[str, bool]
    camera: dict[str, Any] | None
    camera_error: str | None
    detect: dict[str, Any] | None
    detect_error: str | None
    board_model: str | None

    @property
    def has_camera(self) -> bool:
        """True only when a capture source actually opened on the board."""
        return self.capabilities.get("camera") is True and self.camera is not None

    @property
    def can_detect(self) -> bool:
        """True only when the board's YOLO engine loaded and ran its warm-up."""
        return self.capabilities.get("detect") is True and self.detect is not None

    @property
    def is_tegra(self) -> bool:
        """What the daemon found in the board's device tree or release file. False on a laptop
        running the daemon for a test, which is the honest answer there."""
        return self.capabilities.get("tegra") is True

    @property
    def camera_fov_deg(self) -> float | None:
        """The lens's horizontal field of view as the daemon was told it with `--fov-deg`.

        None means nobody said, and callers must treat the geometry as uncalibrated rather than
        quietly assume a lens: the 62 degree default is a Pi camera module's, not this one's."""
        fov = (self.camera or {}).get("fov_deg")
        if isinstance(fov, bool) or not isinstance(fov, (int, float)):
            return None
        return float(fov)

    @property
    def camera_size(self) -> tuple[int, int] | None:
        """The size of the JPEGs `/snapshot.jpg` serves, as (width, height)."""
        size = (self.camera or {}).get("size")
        if isinstance(size, (list, tuple)) and len(size) == 2:
            return int(size[0]), int(size[1])
        return None

    @property
    def camera_fps(self) -> float | None:
        fps = (self.camera or {}).get("fps")
        if isinstance(fps, bool) or not isinstance(fps, (int, float)):
            return None
        return float(fps)

    def label(self) -> str | None:
        """The detector in a few words for a header row, as `yolov8n.pt on cuda`, or None when
        the board has no detector."""
        if not self.can_detect or self.detect is None:
            return None
        model = str(self.detect.get("model") or "a model")
        device = self.detect.get("device")
        return f"{model} on {device}" if device else model

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    def record(self, address: str) -> dict[str, Any]:
        """What a run's `run_start` says about the board it used: where it was, which daemon
        answered, what it could do, and the detector's model and device. Enough to tell two
        runs on two boards apart a month later, and nothing else the daemon said, which is
        `doctor`'s to show."""
        detect = self.detect or {}
        return {
            "address": address,
            "daemon_version": self.daemon_version,
            "capabilities": dict(self.capabilities),
            "detect": (
                {"model": detect.get("model"), "device": detect.get("device")}
                if self.can_detect
                else None
            ),
        }


@dataclass(frozen=True)
class HostBoard:
    """`/board`: the board's own files and command output as raw text, parsed by nobody yet.

    The daemon parses nothing on purpose, so the parsing lives in one place (`quackd/doctor.py`)
    and a board that prints something unexpected is fixed with a quackd release rather than a
    trip to the robot. A value of None has its reason under the same key in `errors`."""

    files: dict[str, str | None]
    nodes: dict[str, bool]
    commands: dict[str, str | None]
    errors: dict[str, str]


@dataclass(frozen=True)
class HostBox:
    """One detection as the board's model reported it: its own class name, and a box in the
    pixels of the image that was sent."""

    name: str
    conf: float
    x1: float
    y1: float
    x2: float
    y2: float

    def as_tuple(self) -> tuple[str, float, float, float, float, float]:
        """The shape `quackd.perception.yolo.detections_from_boxes` takes."""
        return (self.name, self.conf, self.x1, self.y1, self.x2, self.y2)


@dataclass(frozen=True)
class HostDetections:
    """`/detect`'s answer. `w` and `h` are the decoded image's size, which the geometry needs,
    and `ms` is the time the board spent in the model, so a slow round trip can be split into
    the network's share and the GPU's."""

    w: int
    h: int
    boxes: tuple[HostBox, ...]
    model: str | None
    device: str | None
    ms: float | None


# ── reading a reply field by field ──────────────────────────────────────────────────────
#
# Every accessor raises a HostError that names the host, the path and the field, so a daemon of
# another version, or something else answering on its port, is a sentence and never a KeyError
# or a TypeError from three frames down.

_MISSING = object()


def _wrong(where: str, what: str) -> HostError:
    return HostError(f"{where} with a reply of the wrong shape: {what}")


def _kind(value: Any) -> str:
    """The JSON name of a value's type, for a sentence a person reads."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true or false"
    if isinstance(value, (int, float)):
        return "a number"
    if isinstance(value, str):
        return "a string"
    if isinstance(value, list):
        return "a list"
    if isinstance(value, dict):
        return "an object"
    return type(value).__name__


def _clip(text: str, limit: int = 200, *, token: str | None = None) -> str:
    """A string the board sent, made safe to put in a sentence on somebody's terminal: the token
    replaced by `<token>`, control characters (an escape sequence included) turned to spaces,
    and a long one cut short.

    The token goes first because the cut can fall inside an echoed one, and what is left of it
    then is a piece no later replace would find: most of a 64-character token, in a field name
    cut at 60."""
    if token:
        text = text.replace(token, "<token>")
    flat = "".join(c if c.isprintable() else " " for c in text)
    return flat if len(flat) <= limit else flat[: limit - 3] + "..."


def _is_number(value: Any) -> TypeGuard[float]:
    # bool is an int to Python and not a number to JSON; NaN and Infinity are accepted by
    # `json.loads` and would poison every bearing computed from them
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _is_int(value: Any) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _get(obj: dict[str, Any], key: str, where: str, label: str | None) -> Any:
    value = obj.get(key, _MISSING)
    if value is _MISSING:
        raise _wrong(where, f"it has no {label or key!r}")
    return value


def _text(obj: dict[str, Any], key: str, where: str, *, label: str | None = None) -> str:
    value = _get(obj, key, where, label)
    if not isinstance(value, str):
        raise _wrong(where, f"{label or key!r} is {_kind(value)}, not a string")
    return value


def _text_or_none(
    obj: dict[str, Any], key: str, where: str, *, label: str | None = None
) -> str | None:
    value = obj.get(key)
    if value is not None and not isinstance(value, str):
        raise _wrong(where, f"{label or key!r} is {_kind(value)}, not a string or null")
    return value


def _number(obj: dict[str, Any], key: str, where: str, *, label: str | None = None) -> float:
    value = _get(obj, key, where, label)
    if not _is_number(value):
        raise _wrong(where, f"{label or key!r} is {_kind(value)}, not a finite number")
    return float(value)


def _number_or_none(
    obj: dict[str, Any], key: str, where: str, *, label: str | None = None
) -> float | None:
    value = obj.get(key)
    if value is None:
        return None
    if not _is_number(value):
        raise _wrong(where, f"{label or key!r} is {_kind(value)}, not a finite number or null")
    return float(value)


def _integer(obj: dict[str, Any], key: str, where: str, *, label: str | None = None) -> int:
    value = _get(obj, key, where, label)
    if not _is_int(value):
        raise _wrong(where, f"{label or key!r} is {_kind(value)}, not a whole number")
    return int(value)


def _object(obj: dict[str, Any], key: str, where: str) -> dict[str, Any]:
    value = _get(obj, key, where, None)
    if not isinstance(value, dict):
        raise _wrong(where, f"{key!r} is {_kind(value)}, not an object")
    return value


def _object_or_none(obj: dict[str, Any], key: str, where: str) -> dict[str, Any] | None:
    value = obj.get(key)
    if value is not None and not isinstance(value, dict):
        raise _wrong(where, f"{key!r} is {_kind(value)}, not an object or null")
    return value


def _hello_from(payload: dict[str, Any], address: str, token: str | None) -> HostHello:
    where = f"{address} answered /hello"
    # The protocol first, before any other field: something else answering on this port is a
    # wrong port, and saying so beats a complaint about a field it was never going to have.
    protocol = payload.get("protocol")
    if protocol != PROTOCOL:
        said = (
            f"says it speaks {_clip(protocol, 60, token=token)!r}"
            if isinstance(protocol, str)
            else "names no protocol"
        )
        raise HostError(f"{address} is not {PROTOCOL}: its /hello {said}. {_DAEMON_HINT}")
    version = payload.get("protocol_version")
    if not _is_int(version):
        raise _wrong(where, f"'protocol_version' is {_kind(version)}, not a whole number")
    if version > PROTOCOL_VERSION:
        raise HostError(
            f"{address} speaks {PROTOCOL} version {version} and this quackd speaks version "
            f"{PROTOCOL_VERSION}: update quackd on this machine to a release that speaks "
            f"version {version}"
        )
    if version < PROTOCOL_VERSION:
        raise HostError(
            f"{address} speaks {PROTOCOL} version {version} and this quackd speaks version "
            f"{PROTOCOL_VERSION}: update the daemon on the board, by copying "
            "bridge/jetson/quackd_jetson_hostd.py from this quackd's release"
        )

    capabilities: dict[str, bool] = {}
    for name, flag in _object(payload, "capabilities", where).items():
        if not isinstance(flag, bool):
            field = f"'capabilities.{_clip(str(name), 60, token=token)}'"
            raise _wrong(where, f"{field} is {_kind(flag)}, not true or false")
        capabilities[str(name)] = flag

    camera = _object_or_none(payload, "camera", where)
    if camera is not None:
        size = camera.get("size")
        if not (
            isinstance(size, list) and len(size) == 2 and all(_is_int(v) and v > 0 for v in size)
        ):
            raise _wrong(where, "'camera.size' is not [width, height] in whole pixels")
        fov = _number_or_none(camera, "fov_deg", where, label="camera.fov_deg")
        if fov is not None and not 0.0 < fov < 180.0:
            raise _wrong(where, f"'camera.fov_deg' is {fov:g}, which no lens has")
        fps = _number_or_none(camera, "fps", where, label="camera.fps")
        if fps is not None and fps <= 0:
            raise _wrong(where, f"'camera.fps' is {fps:g}, which is no frame rate")
        _text_or_none(camera, "source", where, label="camera.source")
        camera = dict(camera)

    detect = _object_or_none(payload, "detect", where)
    if detect is not None:
        _text(detect, "model", where, label="detect.model")
        _text(detect, "device", where, label="detect.device")
        detect = dict(detect)

    return HostHello(
        protocol=protocol,
        protocol_version=version,
        daemon_version=_text(payload, "daemon_version", where),
        hostname=_text(payload, "hostname", where),
        python=_text(payload, "python", where),
        capabilities=capabilities,
        camera=camera,
        camera_error=_text_or_none(payload, "camera_error", where),
        detect=detect,
        detect_error=_text_or_none(payload, "detect_error", where),
        board_model=_text_or_none(payload, "board_model", where),
    )


def _board_from(payload: dict[str, Any], address: str, token: str | None) -> HostBoard:
    where = f"{address} answered /board"

    def field(key: str, name: Any) -> str:
        return f"'{key}[{_clip(str(name), 60, token=token)}]'"

    def texts(key: str) -> dict[str, str | None]:
        out: dict[str, str | None] = {}
        for name, value in _object(payload, key, where).items():
            if value is not None and not isinstance(value, str):
                raise _wrong(where, f"{field(key, name)} is {_kind(value)}, not text or null")
            out[str(name)] = value
        return out

    nodes: dict[str, bool] = {}
    for name, flag in _object(payload, "nodes", where).items():
        if not isinstance(flag, bool):
            raise _wrong(where, f"{field('nodes', name)} is {_kind(flag)}, not true or false")
        nodes[str(name)] = flag
    errors: dict[str, str] = {}
    for name, reason in _object(payload, "errors", where).items():
        if not isinstance(reason, str):
            raise _wrong(where, f"{field('errors', name)} is {_kind(reason)}, not a string")
        errors[str(name)] = reason
    return HostBoard(files=texts("files"), nodes=nodes, commands=texts("commands"), errors=errors)


def _detections_from(payload: dict[str, Any], address: str, token: str | None) -> HostDetections:
    # `token` is for the signature the other two parsers share: no complaint about /detect's
    # reply quotes the board's text, only the protocol's field names and the kinds of values
    where = f"{address} answered /detect"
    w = _integer(payload, "w", where)
    h = _integer(payload, "h", where)
    if w <= 0 or h <= 0:
        raise _wrong(where, f"the image is {w}x{h}, which has no pixels")
    raw_boxes = _get(payload, "boxes", where, None)
    if not isinstance(raw_boxes, list):
        raise _wrong(where, f"'boxes' is {_kind(raw_boxes)}, not a list")
    boxes: list[HostBox] = []
    for i, raw in enumerate(raw_boxes):
        at = f"boxes[{i}]"
        if not isinstance(raw, dict):
            raise _wrong(where, f"{at!r} is {_kind(raw)}, not an object")
        conf = _number(raw, "conf", where, label=f"{at}.conf")
        if not 0.0 <= conf <= 1.0:
            raise _wrong(where, f"'{at}.conf' is {conf:g}, not a confidence from 0 to 1")
        boxes.append(
            HostBox(
                name=_text(raw, "name", where, label=f"{at}.name"),
                conf=conf,
                x1=_number(raw, "x1", where, label=f"{at}.x1"),
                y1=_number(raw, "y1", where, label=f"{at}.y1"),
                x2=_number(raw, "x2", where, label=f"{at}.x2"),
                y2=_number(raw, "y2", where, label=f"{at}.y2"),
            )
        )
    return HostDetections(
        w=w,
        h=h,
        boxes=tuple(boxes),
        model=_text_or_none(payload, "model", where),
        device=_text_or_none(payload, "device", where),
        ms=_number_or_none(payload, "ms", where),
    )


# ── the client ──────────────────────────────────────────────────────────────────────────


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """The daemon never redirects, and urllib follows a redirect with the request's own headers,
    token included, to wherever the `Location` points. So a 3xx is left to surface as the
    HTTPError it is."""

    def redirect_request(
        self, req: Any, fp: Any, code: Any, msg: Any, headers: Any, newurl: Any
    ) -> urllib.request.Request | None:
        return None


class HostClient:
    """One board's daemon, over HTTP. A request that goes wrong raises `HostError` and nothing
    else, whatever went wrong with it.

    `host` is `--host`'s text as given; `address` is `host:port` as a URL spells it, and is what
    every message names; `base_url` is where the requests go. The token is held privately and
    is sent in the `X-Quackd-Token` header on every request; a token of "" or whitespace means
    no token. The camera bookkeeping (`frames`, `frame_age_s`, `camera_error`) is this client's
    own count of what `snapshot()` saw, in the shape `camera_health()` reports to `doctor`.

    What is not a request going wrong is not a HostError. A bad `host` or an unusable token is a
    ValueError from the constructor, and a `conf` the daemon would refuse is one from `detect`
    (`confidence_floor`): those are the caller's mistakes, found before anything is sent, and
    they are left to surface rather than be caught with the board's failures. A caller that
    takes `conf` from configuration checks it once, where it is configured, so a bad one is
    found before a run and not on every frame of it.

    `token_fix` is what a refused token's message tells the reader to do, from wherever the
    token came from (`HostChoice.token_fix`); without it the message names both the flag and
    the variable."""

    def __init__(
        self, host: str, *, token: str | None = None, token_fix: str | None = None
    ) -> None:
        name, port = parse_host(host)
        self.host = host
        self.address = netloc(name, port)
        self.base_url = f"http://{self.address}"
        self.snapshot_url = f"{self.base_url}{SNAPSHOT_PATH}"
        self._token = clean_token(token)
        self._token_fix = token_fix
        # No proxy, whatever the environment says: HTTP_PROXY is common on a corporate laptop,
        # and a proxy would see the token header and every frame of a board on the local
        # network, which it has no business seeing.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirects())
        self._hello: HostHello | None = None
        self._camera_lock = threading.Lock()
        self._frames = 0
        self._frame_age_s: float | None = None
        self._camera_error: str | None = None

    def __repr__(self) -> str:
        return f"HostClient({self.address!r})"

    @property
    def has_token(self) -> bool:
        return self._token is not None

    @property
    def frames(self) -> int:
        return self._frames

    @property
    def frame_age_s(self) -> float | None:
        return self._frame_age_s

    @property
    def camera_error(self) -> str | None:
        return self._camera_error

    # ── the endpoints ───────────────────────────────────────────────────────────────────

    def hello(self, *, refresh: bool = False) -> HostHello:
        """What the daemon is and can do. Cached after the first answer, because nothing in it
        changes while the daemon runs; `refresh=True` asks again."""
        if self._hello is not None and not refresh:
            return self._hello
        try:
            payload = self._json("GET", "/hello", timeout_s=HELLO_TIMEOUT_S)
        except HostError as e:
            if e.status == 404:
                raise self._fail(
                    f"{self.address} is not {PROTOCOL}: it has nothing at /hello. {_DAEMON_HINT}",
                    status=404,
                ) from e
            raise
        self._refused_unless_ok(payload, "/hello")
        hello = self._parsed(_hello_from, payload)
        self._hello = hello
        return hello

    def healthz(self) -> dict[str, Any]:
        """The daemon's own health, as it sent it but for the token. Always a 200 with `ok` in
        it, so an unhealthy board is an answer here, not an error."""
        return self._json("GET", "/healthz", timeout_s=HELLO_TIMEOUT_S)

    def board(self) -> HostBoard:
        """The board's files and command output, raw, for `doctor`'s parsers."""
        payload = self._json("GET", "/board", timeout_s=BOARD_TIMEOUT_S)
        self._refused_unless_ok(payload, "/board")
        return self._parsed(_board_from, payload)

    def snapshot(self) -> tuple[Image.Image, float | None]:
        """The board camera's newest frame, as an RGB image and its age in seconds.

        The age comes from `X-Frame-Age`, and a frame older than `STALE_AFTER_S` is refused
        rather than returned: a camera that stopped keeps serving its last good frame, and a
        verb steering on a photograph of where the ball used to be is the failure the Open Duck
        daemon was built to prevent. A 503 surfaces the daemon's own sentence ("no frame
        captured yet", "the last frame is 3.2s old ..."), which is far more use to whoever is
        next to the board than a status code."""
        try:
            _, headers, raw = self._exchange("GET", SNAPSHOT_PATH, timeout_s=SNAPSHOT_TIMEOUT_S)
            age = self._frame_age(headers.get("x-frame-age"))
        except HostError as e:
            said = (e.payload or {}).get("age_s")
            self._camera_failed(str(e), float(said) if _is_number(said) else None)
            raise
        if age is not None and age > STALE_AFTER_S:
            stale = self._fail(
                f"the newest frame from {self.address} is {age:.1f}s old (stale after "
                f"{STALE_AFTER_S:g}s); the camera on the board has stopped"
            )
            self._camera_failed(str(stale), age)
            raise stale
        try:
            with Image.open(io.BytesIO(raw), formats=["JPEG"]) as picture:
                image = picture.convert("RGB")
        except Exception as e:
            bad = self._fail(f"{self.address} sent a snapshot that is not a JPEG PIL can read")
            self._camera_failed(str(bad), None)
            raise bad from e
        with self._camera_lock:
            self._frames += 1
            self._frame_age_s = age
            self._camera_error = None
        return image, age

    def detect(self, jpeg: bytes, *, conf: float | None = None) -> HostDetections:
        """Run the board's detector on one JPEG. `conf` overrides the daemon's confidence floor
        for this call only; the boxes come back in the pixels of the image that was sent. A
        `conf` the daemon would refuse is a ValueError, raised before anything is sent
        (`confidence_floor`)."""
        query: dict[str, str] | None = None
        if conf is not None:
            query = {"conf": f"{confidence_floor(conf):g}"}
        body = bytes(jpeg)
        if len(body) > MAX_JPEG_BYTES:
            raise self._fail(
                f"a {len(body) // 1024} KB image is over the {MAX_JPEG_BYTES // (1024 * 1024)} MB "
                f"{self.address} accepts for /detect; send a smaller frame"
            )
        payload = self._json(
            "POST",
            "/detect",
            timeout_s=DETECT_TIMEOUT_S,
            body=body,
            content_type="image/jpeg",
            query=query,
        )
        self._refused_unless_ok(payload, "/detect")
        return self._parsed(_detections_from, payload)

    def camera_health(self) -> dict[str, Any]:
        """What `doctor` renders for a camera, in the shape the Open Duck bridge reports it:
        where the frames come from, how many this client has taken, how old the last one was,
        the age past which one is refused, and the last error, or None."""
        with self._camera_lock:
            return {
                "url": self.snapshot_url,
                "frames": self._frames,
                "age_s": self._frame_age_s,
                "stale_after_s": STALE_AFTER_S,
                "error": self._camera_error,
            }

    # ── the wire ────────────────────────────────────────────────────────────────────────

    def _fail(
        self,
        message: str,
        *,
        status: int | None = None,
        payload: dict[str, Any] | None = None,
        unresolved: bool = False,
    ) -> HostError:
        """Every HostError this client raises is built here, so the token is kept out of all of
        them in one place: replaced in the message, and in every string of the payload, keys
        included, so a caller that shows the payload shows no token either. No message is built
        from the token, but a daemon that echoed it back in a `reason` would otherwise put it on
        somebody's screen.

        The replace in the message is a backstop. The board's text in a message was cut short
        before it got here, and a cut through an echoed token leaves a piece no replace can
        find, which is why every `_clip` of the board's text is given the token too."""
        if self._token:
            message = message.replace(self._token, "<token>")
            payload = self._scrub(payload)
        return HostError(message, status=status, payload=payload, unresolved=unresolved)

    def _scrub(self, value: Any) -> Any:
        """A copy of `value` with the token replaced by `<token>` in every string in it, keys
        included.

        A loop rather than recursion, because `json.loads` can accept nesting deeper than Python
        lets a function recurse (Python 3.12 read nearly three thousand levels where this was
        written, against a default recursion limit of a thousand), and a recursive copy of a
        payload that deep would fail with an error that is not a HostError."""
        token = self._token
        if token is None:
            return value
        todo: list[tuple[Any, Any]] = []

        def copied(item: Any) -> Any:
            if isinstance(item, str):
                return item.replace(token, "<token>")
            if isinstance(item, dict):
                empty: Any = {}
            elif isinstance(item, list):
                empty = []
            else:
                return item
            todo.append((item, empty))
            return empty

        scrubbed = copied(value)
        while todo:
            original, copy = todo.pop()
            if isinstance(original, dict):
                for key, item in original.items():
                    copy[copied(key)] = copied(item)
            else:
                copy.extend(copied(item) for item in original)
        return scrubbed

    def _parsed(
        self, parse: Callable[[dict[str, Any], str, str | None], _T], payload: dict[str, Any]
    ) -> _T:
        """A reply read into its dataclass, with any complaint about its shape scrubbed like every
        other message. `from None`, because a chained original would print the unscrubbed text
        in a traceback anyway."""
        try:
            return parse(payload, self.address, self._token)
        except HostError as e:
            raise self._fail(str(e), status=e.status, payload=e.payload) from None

    def _camera_failed(self, error: str, age: float | None) -> None:
        with self._camera_lock:
            self._camera_error = error
            self._frame_age_s = age

    def _frame_age(self, header: str | None) -> float | None:
        if header is None:
            return None
        try:
            age = float(header)
        except ValueError:
            age = math.nan
        if not math.isfinite(age) or age < 0:
            said = _clip(header, token=self._token)
            raise self._fail(f"{self.address} stamped its frame with an age of {said!r}")
        return age

    def _refused_unless_ok(self, payload: dict[str, Any], path: str) -> None:
        if payload.get("ok") is False:
            reason = payload.get("reason")
            said = (
                _clip(reason, token=self._token) if isinstance(reason, str) else "no reason given"
            )
            raise self._fail(f"{self.address} refused {path}: {said}", payload=payload)

    def _json(
        self,
        method: str,
        path: str,
        *,
        timeout_s: float,
        body: bytes | None = None,
        content_type: str | None = None,
        query: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        _, _, raw = self._exchange(
            method, path, timeout_s=timeout_s, body=body, content_type=content_type, query=query
        )
        try:
            payload = json.loads(raw.decode("utf-8"))
        # RecursionError too: that is how `json.loads` meets ten thousand nested brackets
        except (UnicodeDecodeError, ValueError, RecursionError) as e:
            raise self._fail(
                f"{self.address} answered {path} with something that is not JSON, so that port "
                f"is not {PROTOCOL}: {_DAEMON_HINT}"
            ) from e
        if not isinstance(payload, dict):
            raise self._fail(
                f"{self.address} answered {path} with {_kind(payload)} where {PROTOCOL} sends a "
                "JSON object"
            )
        # Every reply, not only the refusals `_fail` sees: a daemon that echoed the token in a
        # hostname, a reason or a board file would otherwise hand it to whatever shows the
        # reply, and `doctor` shows all of it, on the screen and in --json.
        scrubbed: dict[str, Any] = self._scrub(payload)
        return scrubbed

    def _exchange(
        self,
        method: str,
        path: str,
        *,
        timeout_s: float,
        body: bytes | None = None,
        content_type: str | None = None,
        query: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        """One request, as (status, headers with lower-case names, body).

        Each wait on the socket is bounded by `timeout_s`, which bounds a dead daemon, a board
        that dropped off the network and a daemon that hangs mid-reply alike. Two waits are not.
        A reply trickled in a byte at a time could take longer, which nothing but a hostile board
        would do. And looking up the board's name happens before there is a socket, on the
        system resolver's own clock, so a name that has stopped resolving can take longer than
        `timeout_s` to fail, on every call; an address in `--host` skips the lookup."""
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        headers = {"Accept": "application/json, image/jpeg"}
        if self._token is not None:
            headers[TOKEN_HEADER] = self._token
        if content_type is not None:
            headers["Content-Type"] = content_type
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with self._opener.open(request, timeout=timeout_s) as resp:
                # In chunks up to the cap, rather than one read of the cap: a reply with no
                # Content-Length would have `read(n)` set aside all n bytes before the first one
                # arrived, sixteen megabytes for every frame of a camera
                chunks: list[bytes] = []
                size = 0
                while chunk := resp.read(_READ_CHUNK):
                    size += len(chunk)
                    if size > MAX_REPLY_BYTES:
                        raise self._fail(
                            f"{self.address} sent more than {MAX_REPLY_BYTES // (1024 * 1024)} MB "
                            f"for {path}, which no reply of {PROTOCOL}'s comes near"
                        )
                    chunks.append(chunk)
                headers = {k.lower(): v for k, v in resp.headers.items()}
                return int(resp.status), headers, b"".join(chunks)
        except HostError:
            raise
        except urllib.error.HTTPError as e:
            raise self._refusal(e, path) from e
        except urllib.error.URLError as e:
            raise self._unreachable(e.reason, path, timeout_s) from e
        except (OSError, http.client.HTTPException, ValueError) as e:
            raise self._unreachable(e, path, timeout_s) from e

    def _unreachable(self, reason: Any, path: str, timeout_s: float) -> HostError:
        if isinstance(reason, TimeoutError):
            return self._fail(
                f"{self.address} did not answer {path} within {timeout_s:g}s: is the board up, "
                f"and is {PROTOCOL} running on it?"
            )
        if isinstance(reason, ConnectionRefusedError):
            return self._fail(f"nothing is listening at {self.address}: {_DAEMON_HINT}")
        if isinstance(reason, socket.gaierror):
            return self._fail(
                f"{self.address}: no machine by that name could be found "
                f"({reason.strerror or reason})",
                unresolved=True,
            )
        said = _clip(str(reason), token=self._token) if str(reason) else type(reason).__name__
        return self._fail(f"{self.address} could not be reached for {path}: {said}")

    def _refusal(self, e: urllib.error.HTTPError, path: str) -> HostError:
        try:
            parsed = json.loads(e.read(64 * 1024).decode("utf-8"))
        except Exception:
            parsed = None
        payload = parsed if isinstance(parsed, dict) else None
        reason = (payload or {}).get("reason")
        said = (
            _clip(reason, token=self._token) if isinstance(reason, str) and reason.strip() else None
        )
        if e.code == 401:
            if self._token is None:
                message = (
                    f"{self.address} wants a token and none was given: pass --host-token or set "
                    f"{TOKEN_ENV}"
                )
            else:
                # a client built with no idea where its token came from names both places
                fix = self._token_fix or HostChoice().token_fix
                message = f"{self.address} refused the token it was given: {fix}"
        elif 300 <= e.code < 400:
            message = (
                f"{self.address} answered {path} with a redirect, which {PROTOCOL} never sends, "
                "so it was not followed"
            )
        elif said:
            message = f"{self.address} says: {said}"
        else:
            message = f"{self.address} answered {path} with HTTP {e.code}"
        return self._fail(message, status=e.code, payload=payload)
