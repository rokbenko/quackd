"""`roboglot doctor`: what can run here, and what this machine is assuming about the robot.

It exists because "it doesn't work" almost always means a missing extra, a missing key, or
an upstream assumption, and all three should be visible in one screen before anyone opens
an issue.

It is two halves on purpose. `collect` asks the questions — which modules import, which keys
are set, which local servers answer, what a real robot says about itself, what a board named
with `--host` says about itself — and answers in dataclasses with no styling anywhere in them.
`render` decides what that looks like, and `to_dict` is the same report with no renderer at
all. That split is what `--json` is made of, and it was not possible before: every cell in
here used to *be* a markup string, so there was nothing underneath to serialise.
"""

from __future__ import annotations

import contextlib
import functools
import importlib
import importlib.metadata as md
import os
import platform
import re
import socket
import threading
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from rich.console import Console
from rich.rule import Rule
from rich.text import Text

from roboglot import __version__, ui
from roboglot.adapters.base import AdapterError, RestResult, go_to_rest_if_any
from roboglot.adapters.factory import (
    RobotSpec,
    adapter_names,
    describe,
    is_installed,
    list_adapters,
    parse_robot_spec,
)
from roboglot.adapters.factory import (
    _module as _adapter_module,
)
from roboglot.agent.providers.base import ProviderError
from roboglot.agent.providers.factory import (
    DEFAULT_LLMS,
    EXTRA_FOR,
    KEY_ENV,
    KEY_FALLBACKS,
    LOCAL_NAMES,
    PROVIDER_NAMES,
    SDK_FOR,
    parse_llm,
    resolve_llm_id,
)
from roboglot.agent.providers.local import PRESETS, on_host
from roboglot.boards import l4t as _l4t
from roboglot.command import HIDDEN, redacted_url
from roboglot.glotfile.parser import list_bundled_tasks
from roboglot.host import (
    PROTOCOL,
    HostBoard,
    HostClient,
    HostError,
    HostHello,
    host_of,
    parse_host,
)

# The optional extras table, which is about packages rather than providers: the providers table
# builds its own rows from SDK_FOR and EXTRA_FOR. One wheel now serves ten vendors, so naming
# them all here would be a list to keep in step for no gain; `roboglot list-models` and the
# providers table above already say which vendor wants which install.
EXTRAS = {
    "anthropic": ("anthropic", "roboglot[anthropic]"),
    "openai": ("openai", "roboglot[openai] and every OpenAI-compatible vendor"),
    "gemini": ("google.genai", "roboglot[gemini]"),
    "decision (System One client)": ("typesafe_sdk", "roboglot[decision]"),
    "laya (in-process decision LLM)": ("laya", "roboglot[laya]"),
    "yolo": ("ultralytics", "roboglot[yolo]"),
    "lan (zeroconf)": ("zeroconf", "roboglot[lan]"),
    "lan (mqtt)": ("paho.mqtt.client", "roboglot[lan]"),
    "lerobot": ("lerobot", "roboglot[lerobot]"),
    "lerobot (feetech bus)": ("rustypot", "roboglot[lerobot]"),
    "lerobot-vla (transformers)": ("transformers", "roboglot[lerobot-vla]"),
    "rosbridge": ("roslibpy", "roboglot[rosbridge]"),
    "rosarm (roslibpy)": ("roslibpy", "roboglot[rosarm]"),
    "microduck camera (webrtc)": ("aiortc", "roboglot[microduck-camera]"),
    "xlerobot": ("zmq", "roboglot[xlerobot]"),
    "alohamini": ("zmq", "roboglot[alohamini]"),
    "ui (the page roboglot ui serves)": ("streamlit", "roboglot[ui]"),
}
# Packages looked up by distribution metadata only, never imported: importing lerobot pulls
# torch into a diagnostics command, which is exactly what doctor is not. rustypot is the half of
# `roboglot[lerobot]` that opens the serial port and drives the arm's motors, a native library
# doctor has no reason to load, and a lerobot installed without its `[feetech]` extra imports
# cleanly and then cannot build a follower, so doctor asks for it by name.
# Laya is here for the same reason as lerobot rather than for its own: it is the one decision
# LLM that runs in this process, so it carries torch too, and the decision table below prints a
# version for every row on a machine that is only reading the table. transformers is the half
# of `roboglot[lerobot-vla]` the arm's own extra lacks, the one a SmolVLA or a pi05 checkpoint
# loads its tokenizer with in the policy server, and importing it costs seconds. ultralytics
# brings torch too, and this table is read after the arm's close, with nothing moving and
# maybe somebody still pressing Ctrl-C (`safety.AfterTheClose`).
_SDK_BY_METADATA = {
    "anthropic": "anthropic",
    "openai": "openai",
    "google.genai": "google-genai",
    "typesafe_sdk": "typesafe-sdk",
    "zeroconf": "zeroconf",
    "paho.mqtt.client": "paho-mqtt",
    "roslibpy": "roslibpy",
}
"""The provider SDKs and the extras that are Python alone, read by their distribution's metadata
too. Importing one proved only that its files were there, which the metadata the installer wrote
says as well, and cost doctor a fraction of a second or more each, one after another: the run
that uses one imports it itself and says so when that fails. Every core package is still
imported, since that import is the check that Roboglot itself can start, and so are aiortc and
pyzmq, which load a native library that can be missing or built for another Python while the
metadata says the package is installed."""

_METADATA_ONLY = {
    "lerobot": "lerobot",
    "rustypot": "rustypot",
    "laya": "laya",
    "transformers": "transformers",
    "streamlit": "streamlit",
    "ultralytics": "ultralytics",
    **_SDK_BY_METADATA,
}

CORE_MODULES = (
    ("pydantic", "pydantic"),
    ("mcp", "mcp"),
    ("numpy", "numpy"),
    ("Pillow", "PIL"),
)

Progress = Any
"""`progress(message)`, or None: what the spinner says while a slow question is asked."""

_ADAPTER_HINT = "roboglot list-adapters shows the ten that ship and their backends"

TEAM_NOTE = (
    "team mode (--team): one LLM pilot per robot, in-process bus by default. The MQTT bus "
    "(roboglot[lan]) is library-only (docs/guides/lan.md)."
)

NO_DROP_QUESTION = (
    "doctor stopped on the error above, so it asked nothing about dropping the arm into your "
    "hands: the line before this one is what its close did with the torque"
)
"""Said after the close note on a probe that failed after the connect, where the CLI's one
question whether to drop the arm (`before_close`) is never reached."""


_DISTRIBUTION_OF = {
    "google.genai": "google-genai",
    "paho.mqtt.client": "paho-mqtt",
    "PIL": "pillow",
    "zmq": "pyzmq",
}
"""Import names whose distribution has another name and only that one, so finding its version never
has to walk every installed package (`_packages_distributions`)."""


@functools.cache
def _packages_distributions() -> Mapping[str, list[str]]:
    """Which distribution provides each import name. Reading every installed package's file list
    takes over a second, and a report asks for this once per import name that needs it, so it is
    read once."""
    return md.packages_distributions()


def _installed(module: str) -> str | None:
    if module in _METADATA_ONLY:
        try:
            return md.version(_METADATA_ONLY[module])
        except md.PackageNotFoundError:
            return None
    try:
        importlib.import_module(module)
    except Exception:
        return None
    dist = _DISTRIBUTION_OF.get(module, module)
    try:
        return md.version(dist)
    except md.PackageNotFoundError:
        pass
    # An import name is not always a distribution name: `yaml` ships as pyyaml. Ask the installer
    # which one it was, rather than printing "?" next to a package that is plainly installed and
    # working.
    for candidate in _packages_distributions().get(module.split(".")[0], []):
        try:
            return md.version(candidate)
        except md.PackageNotFoundError:
            continue
    return "?"


def _mask(value: str) -> str:
    return value[:4] + "…" + value[-2:] if len(value) > 8 else "set"


# ── what the report is made of ──────────────────────────────────────────────────────────


@dataclass
class Check:
    """One yes-or-no fact with its evidence: a module and its version, an extra and how to
    install it."""

    name: str
    ok: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


@dataclass
class ProviderRow:
    name: str
    extra: str
    version: str | None
    key: str
    """Masked, or empty when there is none."""
    key_env: str
    key_optional: bool
    """A local server does not need one; a cloud provider cannot run without it."""
    llm_id: str
    url: str = ""
    """Where a decision LLM listens, and empty for everything that has no address: every
    provider, whose vendor owns its own, and `jev`, whose SDK does. `openai` has one, because
    its backend always hands the SDK the row's address rather than letting it find one."""
    pinned: bool = False
    """The environment named this LLM rather than the row taking its default: ROBOGLOT_LLM for
    a provider, the SDK's own variable for a decision LLM that honours one."""
    refused_llm: str = ""
    """A ROBOGLOT_LLM whose LLM half this vendor does not list, which is why `model` is the
    vendor's default and not what the environment asked for."""
    key_from: str = ""
    """The second name the key was found under (`KEY_FALLBACKS`), when `key_env` was empty: the
    provider reads it there too, so a row that said `key_env unset` would be wrong about a run."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "extra": self.extra,
            "version": self.version,
            "key": self.key or None,
            "key_env": self.key_env,
            "key_from": self.key_from or None,
            "key_optional": self.key_optional,
            "llm_id": self.llm_id,
            "url": self.url or None,
            "pinned": self.pinned,
            "refused_llm": self.refused_llm or None,
        }


@dataclass
class PlacementRow:
    """One LLM Ollama has loaded, and how much of it sits in GPU memory.

    Ollama's own `/api/ps` reports both sizes, and the gap between them is the question a
    Jetson owner cannot see from the laptop: an LLM that answers, slowly, because it is on the
    CPU looks exactly like one that answers quickly until somebody times it."""

    llm_id: str
    size: int
    """Bytes the loaded LLM takes, as Ollama counts them."""
    size_vram: int
    """Bytes of that in GPU memory. Equal to `size` is all on the GPU, zero is all on the CPU."""

    @property
    def gpu_pct(self) -> int:
        """The share on the GPU, rounded down, and never 0 for an LLM with any of itself there:
        a partial split must not read as "all on the CPU" nor as "all on the GPU"."""
        if self.size <= 0 or self.size_vram <= 0:
            return 0
        if self.size_vram >= self.size:
            return 100
        return max(1, min(99, self.size_vram * 100 // self.size))

    @property
    def where(self) -> str:
        """gpu · partial · cpu"""
        pct = self.gpu_pct
        return "gpu" if pct == 100 else "cpu" if pct == 0 else "partial"

    def to_dict(self) -> dict[str, Any]:
        return {
            "llm_id": self.llm_id,
            "size": self.size,
            "size_vram": self.size_vram,
            "gpu_pct": self.gpu_pct,
            "where": self.where,
        }


@dataclass
class ServerRow:
    preset: str
    url: str
    """Where it was probed, with any password in it replaced: the screen is pasted into issues."""
    state: str
    """up · http · down · unset · skipped (the board's name has no address, so nothing was
    asked)"""
    detail: str = ""
    placement: list[PlacementRow] | None = None
    """What Ollama has loaded and where, for the ollama row when it is up; None when nobody
    asked, which is every other row."""
    placement_note: str = ""
    """Why `placement` is empty when it was asked for: nothing loaded, or `/api/ps` failed."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "preset": self.preset,
            "url": self.url,
            "state": self.state,
            "detail": self.detail,
            "placement": (
                None if self.placement is None else [p.to_dict() for p in self.placement]
            ),
            "placement_note": self.placement_note or None,
        }


@dataclass
class VerbRow:
    name: str
    core: bool
    safety: str
    preconditions: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "core": self.core,
            "safety": self.safety,
            "preconditions": self.preconditions,
        }


@dataclass
class ProbeRow:
    what: str
    value: str
    state: str = "plain"
    """plain · ok · warn · fail"""

    def to_dict(self) -> dict[str, Any]:
        return {"what": self.what, "value": self.value, "state": self.state}


@dataclass
class ProbeReport:
    """What a real robot said about itself.

    The only way to see the difference between Roboglot's description of a fully built robot and
    the one somebody assembled, before a run finds it. It is one of the parts of doctor that
    leave this machine, with the LLM servers it probes and the board `--host` names."""

    address: str
    ok: bool
    rows: list[ProbeRow] = field(default_factory=list)
    advisories: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "address": self.address,
            "ok": self.ok,
            "rows": [r.to_dict() for r in self.rows],
            "advisories": self.advisories,
            "error": self.error,
        }


@dataclass
class RobotReport:
    spec: str
    summary: str = ""
    verbs: list[VerbRow] = field(default_factory=list)
    probe: ProbeReport | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec": self.spec,
            "summary": self.summary,
            "verbs": [v.to_dict() for v in self.verbs],
            "probe": self.probe.to_dict() if self.probe else None,
            "error": self.error,
        }


@dataclass
class TransportRow:
    name: str
    status: str
    note: str = ""
    found: bool = False
    """Something this transport needs is here: a robotd socket on the machine, say."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "note": self.note,
            "found": self.found,
        }


@dataclass
class Assumption:
    upstream: str
    what: str
    note: str

    def to_dict(self) -> dict[str, Any]:
        return {"upstream": self.upstream, "what": self.what, "note": self.note}


@dataclass
class PinRow:
    """Where an upstream was read, when, and how much of it anybody has actually run."""

    upstream: str
    pin: str
    extra_pin: str = ""
    """A second commit where an upstream has one: microduck_rl pins its policies apart from
    its model, and they move independently."""
    read_on: str = ""
    verified: int = 0
    unverified: int = 0
    never_run: str = ""
    doc: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "upstream": self.upstream,
            "pin": self.pin,
            "extra_pin": self.extra_pin,
            "read_on": self.read_on,
            "verified": self.verified,
            "unverified": self.unverified,
            "never_run": self.never_run,
            "doc": self.doc,
        }


@dataclass
class JetsonReport:
    """What an NVIDIA Jetson says about itself, for whoever is putting an LLM on it.

    Read over the network from the laptop `--host` runs on, and the daemon it ships for it
    (`bridge/jetson/roboglot_jetson_hostd.py`) sends the board's own files and command output as
    raw text for `_jetson_from_dump` to parse here. Informational, and never part of `ok`: the
    GPU belongs to the LLM server and the daemon's detector, and a board with no swap is a
    board this command has advice for, not a machine that cannot run anything.
    """

    board: str | None = None
    """`/proc/device-tree/model` on the board, which names the board rather than the image. A
    daemon run in a container reads it only when the container is privileged or started with
    `--security-opt systempaths=unconfined`: `/proc/device-tree` points into `/sys/firmware`,
    which Docker masks by default."""
    l4t: str | None = None
    """`36.4.3`, parsed from the board's `/etc/nv_tegra_release`."""
    release_seen: bool = False
    """The daemon could read `/etc/nv_tegra_release`. Absent and unparseable are different
    facts and the renderer says which: the first is a daemon that cannot see the board's root
    filesystem (a container, or an unusual install), the second is a release this build has
    not met."""
    jetpack: str | None = None
    mem_total_bytes: int | None = None
    mem_available_bytes: int | None = None
    swap_total_bytes: int | None = None
    """None where the file could not be read. Zero would have said a board with no swap, which
    is a different fact and one worth warning about."""
    swap_devices: list[str] = field(default_factory=list)
    gpu_device: str | None = None
    """The first GPU device node the daemon found on the board, or None for none of them."""
    power_mode: str | None = None
    gpu_busy_pct: int | None = None
    """The GPU's load, `GR3D_FREQ N%` in the one line of `tegrastats` the daemon read."""
    tegrastats: str | None = None
    """That line, whole, because it is the one line the Jetson page asks people to send back."""
    errors: dict[str, str] = field(default_factory=dict)
    """Why the daemon could not read a file or run a command, in its words, keyed as `/board`
    keys them, so an unknown row can say which it was."""

    @property
    def swap_only_zram(self) -> bool:
        """Every swap on the board compresses RAM rather than adding any.

        JetPack ships zram on, which is the right default for a desktop and the wrong one for
        an LLM that does not fit: compressing memory cannot hold what memory could not."""
        return bool(self.swap_devices) and all(d.startswith("/dev/zram") for d in self.swap_devices)

    def to_dict(self) -> dict[str, Any]:
        return {
            "board": self.board,
            "l4t": self.l4t,
            "release_seen": self.release_seen,
            "jetpack": self.jetpack,
            "mem_total_bytes": self.mem_total_bytes,
            "mem_available_bytes": self.mem_available_bytes,
            "swap_total_bytes": self.swap_total_bytes,
            "swap_devices": self.swap_devices,
            "swap_only_zram": self.swap_only_zram,
            "gpu_device": self.gpu_device,
            "power_mode": self.power_mode,
            "gpu_busy_pct": self.gpu_busy_pct,
            "tegrastats": self.tegrastats,
            "errors": dict(self.errors),
        }


@dataclass
class HostReport:
    """The machine `--host` names, as the daemon Roboglot ships for it described it.

    `ok` is one question: did the daemon answer `/hello`, with the token it was given, in the
    protocol this Roboglot speaks. That is what a run needs before it will start with `--host`,
    so it is what fails this report, the way a robot that does not answer `--address` fails it.
    Everything the daemon says after that is information: its health, a camera that stopped, a
    board with no swap. A `/healthz` or `/board` that fails once `/hello` has answered is kept
    as an error of its own and shown as a warning, because a run would still start.

    The token is never here: `HostClient` replaces it in every error it raises and in every
    reply it returns, so a daemon that echoes it in a reason, a hostname or a board file puts
    `<token>` on the screen and in --json, and nothing else in this report is built from it."""

    host: str
    """As it was given: `--host`, a registered robot's host, or `ROBOGLOT_HOST`. Empty when the
    value was refused before anything was asked (`refused_host`), since a refused one is never
    shown."""
    address: str = ""
    """`host:port` as a URL spells it, or empty when `host` is not a machine at all."""
    ok: bool = False
    error: str | None = None
    tried: str = ""
    """The first URL asked, so a daemon that did not answer is reported with where it was
    looked for, not only with what went wrong."""
    hello: HostHello | None = None
    healthz: dict[str, Any] | None = None
    """The daemon's own health, as it sent it but for the token, which the client replaced."""
    healthz_error: str | None = None
    board_error: str | None = None
    unresolved: bool = False
    """True when `host` found no address, so no LLM server there is asked either: each would
    wait on the same failed lookup and then read as a machine whose servers are off. Not in
    --json, where `error` says it in words and every preset row says it was not asked."""
    jetson: JetsonReport | None = None
    """None when the board is not a Tegra, or its dump could not be had."""
    servers: list[ServerRow] = field(default_factory=list)
    """The four local presets as they were probed on this machine, Ollama's placement rows
    among them. The same rows the report's own `servers` lists, kept here as well so a reader
    of `--json` finds the board's LLM server under the board: whether an LLM sits on the
    Jetson's GPU is a fact about the board, and the top-level list also holds `local`, which
    is wherever ROBOGLOT_BASE_URL points and may be no part of it."""

    @property
    def is_tegra(self) -> bool:
        """The daemon's word, or the board's own files: either one is a Jetson."""
        return self.jetson is not None or (self.hello is not None and self.hello.is_tegra)

    def to_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "address": self.address,
            "ok": self.ok,
            "error": self.error,
            "tried": self.tried or None,
            "hello": self.hello.to_dict() if self.hello else None,
            "healthz": self.healthz,
            "healthz_error": self.healthz_error,
            "board_error": self.board_error,
            "jetson": self.jetson.to_dict() if self.jetson else None,
            "servers": [s.to_dict() for s in self.servers],
        }


@dataclass
class DoctorReport:
    version: str
    python: str
    platform: str
    api_version: str = ""
    """The duck-ipc-proto version Roboglot speaks. The one number here that is about the wire
    rather than about this machine, and the first thing to check against a robot."""
    core: list[Check] = field(default_factory=list)
    bundled_tasks: int = 0
    providers: list[ProviderRow] = field(default_factory=list)
    llm_env_error: str = ""
    """Why ROBOGLOT_LLM names no row of the table above, when it names none.

    A vendor that does not exist stops a run before it starts, and the providers table cannot
    show that on its own: the row it would have pinned is the row that is missing."""
    steppers: list[ProviderRow] = field(default_factory=list)
    """Decision LLMs. Not providers, and kept out of that list on purpose.

    A decision LLM answers typed questions about a state and generates nothing, so it can
    never pilot a robot and must never appear under `--llm`. The concrete reason for the
    separate list is `cloud_keys`, which reads `providers` to say "no key for ...": a machine
    with no TypeSafe key is not a machine with a problem, because no decision LLM runs unless
    somebody names one."""
    servers: list[ServerRow] = field(default_factory=list)
    adapters: list[dict[str, Any]] = field(default_factory=list)
    transports: list[TransportRow] = field(default_factory=list)
    extras: list[Check] = field(default_factory=list)
    assumptions: list[Assumption] = field(default_factory=list)
    pins: list[PinRow] = field(default_factory=list)
    robot: RobotReport | None = None
    host: HostReport | None = None
    """None unless a board was named: `--host`, a registered robot's host, or `ROBOGLOT_HOST`."""

    @property
    def ok(self) -> bool:
        """Whether this machine is in a state to run anything: the core imports, the robot
        the command was asked about answered, and so did the board `--host` names. A missing
        extra is a choice, not a fault, and never fails the check; nor does anything the
        board reports about itself once it has answered."""
        if any(not c.ok for c in self.core):
            return False
        if self.robot is not None and self.robot.error is not None:
            return False
        if self.host is not None and not self.host.ok:
            return False
        return not (self.robot and self.robot.probe and not self.robot.probe.ok)

    @property
    def missing_core(self) -> list[str]:
        return [c.name for c in self.core if not c.ok]

    @property
    def cloud_keys(self) -> list[str]:
        """Providers that would run here if they had a key."""
        return [p.name for p in self.providers if not p.key_optional and not p.key]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "version": self.version,
            "python": self.python,
            "platform": self.platform,
            "api_version": self.api_version,
            "host": self.host.to_dict() if self.host else None,
            "core": [c.to_dict() for c in self.core],
            "bundled_tasks": self.bundled_tasks,
            "providers": [p.to_dict() for p in self.providers],
            "llm_env_error": self.llm_env_error or None,
            "steppers": [s.to_dict() for s in self.steppers],
            "servers": [s.to_dict() for s in self.servers],
            "adapters": self.adapters,
            "transports": [t.to_dict() for t in self.transports],
            "extras": [e.to_dict() for e in self.extras],
            "assumptions": [a.to_dict() for a in self.assumptions],
            "pins": [p.to_dict() for p in self.pins],
            "robot": self.robot.to_dict() if self.robot else None,
        }


# ── asking ──────────────────────────────────────────────────────────────────────────────

PROBE_TIMEOUT_S = 1.5
"""How long one probe of an LLM server waits on its socket for any one step, and then for the
whole of the reply's body: the timeout the section titles name."""

REPLY_LIMIT_BYTES = 1 << 20
"""The most a probe reads of a reply. A list of LLMs is a few kilobytes even from a server that
holds hundreds, so a mebibyte is past any real one, and small enough that a reply that never
ends cannot fill this machine's memory first."""

PROBES_DEADLINE_S = 4 * PROBE_TIMEOUT_S
"""When the probes asked together have all answered or are given up on. A probe that keeps to
its timeouts takes at most one to connect (every address at once, `_connection_to`), one for
the reply's headers, one for its body, and one for the read in flight when the body's deadline
passes. Only a server sending its headers a byte at a time takes longer, and that is not a
server to wait for."""

_clock = time.monotonic
"""The clock every deadline here is kept on, a module attribute so a test can move it."""


def _addresses(host: str, port: int) -> list[Any]:
    """Every address `host` has, as `getaddrinfo` gives them."""
    return socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)


def _connect_one(info: Any, timeout: float | None) -> socket.socket:
    """A socket connected to one of `_addresses`, or the OSError the connect raised."""
    family, kind, proto, _name, address = info
    sock = socket.socket(family, kind, proto)
    try:
        sock.settimeout(timeout)
        sock.connect(address)
    except BaseException:
        sock.close()
        raise
    return sock


def _connection_to(
    address: tuple[str, int], timeout: float | None = None, source_address: Any = None
) -> Any:
    """`socket.create_connection`, with every address the name has asked at once.

    `create_connection` asks them in turn, each with the whole timeout. The presets are
    `localhost`, which is `::1` and `127.0.0.1`, and a closed loopback port on Windows takes all
    of its timeout to say so, so a server on one of them was asked only after the other had used
    up its 1.5 s, and a machine running none waited twice. The first address to connect is the
    one used, and a later one is closed as it connects; when none does, the first error is
    raised, as `create_connection` raises it. `source_address` is never set by the probes."""
    host, port = address
    infos = _addresses(host, port)
    lock = threading.Lock()
    done = threading.Event()
    won: list[Any] = []
    failed: list[OSError] = []

    def attempt(info: Any) -> None:
        try:
            sock = _connect_one(info, timeout)
        except OSError as e:
            with lock:
                failed.append(e)
                if len(failed) == len(infos):
                    done.set()
            return
        with lock:
            if not won:
                won.append(sock)
                done.set()
                return
        sock.close()  # an address that connected after another had

    for info in infos:
        threading.Thread(
            target=attempt, args=(info,), name="roboglot-doctor-connect", daemon=True
        ).start()
    if infos:
        done.wait()
    if won:
        return won[0]
    if failed:
        raise failed[0]
    raise OSError(f"getaddrinfo found no address for {host}")


@functools.cache
def _opener() -> Any:
    """urllib's own opener, its proxies and redirects as `urlopen` has them, with a plain http
    connection made through `_connection_to`. An https server is one somebody named, and is
    asked as before."""
    import http.client
    import urllib.request

    class EveryAddressAtOnce(http.client.HTTPConnection):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self._create_connection = _connection_to

    class Handler(urllib.request.HTTPHandler):
        def http_open(self, req: Any) -> Any:
            return self.do_open(EveryAddressAtOnce, req)

    return urllib.request.build_opener(Handler)


def _open(url: str, timeout: float) -> Any:
    """`urllib.request.urlopen(url, timeout=timeout)`, through `_opener`."""
    return _opener().open(url, timeout=timeout)


class _TooLong(Exception):
    """A reply past `REPLY_LIMIT_BYTES`."""


class _TooSlow(Exception):
    """A reply whose body had not ended by its deadline."""


def _read_reply(r: Any, timeout_s: float) -> bytes:
    """The body of `r`, read no further than `REPLY_LIMIT_BYTES` and for no longer than
    `timeout_s` from now.

    `r.read()` with no bound was the whole read: the socket's timeout bounds each wait for a
    byte, not the reply, so a server sending a byte at a time, or one that never ends its body,
    held doctor for as long as it went on. `read1` returns what one wait for the socket gave, so
    the deadline is looked at between them."""
    deadline = _clock() + timeout_s
    read = getattr(r, "read1", None) or r.read
    got = bytearray()
    while chunk := read(REPLY_LIMIT_BYTES + 1 - len(got)):
        got += chunk
        if len(got) > REPLY_LIMIT_BYTES:
            raise _TooLong
        if _clock() > deadline:
            raise _TooSlow
    return bytes(got)


_MIB = f"{REPLY_LIMIT_BYTES >> 20} MiB"


def _probe_llms(base_url: str, timeout_s: float = PROBE_TIMEOUT_S) -> tuple[str, str]:
    """Reachability of an OpenAI-compatible server, as (state, detail). Never raises.

    Whatever answered is read with no trust in its shape. A reply that is JSON but not an
    object, or an object whose `data` is null or a number, used to raise out of `collect()` from
    outside the `try`, which took the whole report with it; an LLM server on another machine
    makes a stranger answer likelier, and doctor is the command people run when something is
    already wrong. `"data": null` is read as no LLMs, the way `"data": []` is: a server with
    nothing to list may say either, and neither is a stranger. Any other `data` that is not a
    list gets the row that says so, where text or an object there used to read as a server with
    no LLMs loaded."""
    import json
    import urllib.error

    try:
        with _open(f"{base_url.rstrip('/')}/models", timeout_s) as r:
            payload = json.loads(_read_reply(r, timeout_s).decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        return "http", f"HTTP {e.code}"
    except _TooLong:
        return "http", f"answered /models with more than {_MIB}"
    except _TooSlow:
        return "http", f"did not finish its /models reply in {timeout_s:g} s"
    except Exception:
        # A body that is not JSON lands here too, and reads as no LLM server there, which is
        # what a dev web server's index page on 8000 or 8080 is. That is the row this probe has
        # always given it; only the JSON shapes below have rows of their own.
        return "down", "not running"
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(payload, dict) or not isinstance(data, list | None):
        return "http", "answered /models with something that is not a list of LLMs"
    ids = [str(m.get("id", "")) for m in data or [] if isinstance(m, dict)]
    shown = ", ".join(i for i in ids[:3] if i)
    more = f" (+{len(ids) - 3})" if len(ids) > 3 else ""
    return "up", f"{shown}{more}" if ids else "no LLMs loaded"


_LATE_LLMS = ("http", f"did not finish answering in {PROBES_DEADLINE_S:g} s")
"""The row of a server whose probe was still asking at `PROBES_DEADLINE_S`."""


def _probe_together(urls: Sequence[str]) -> dict[str, tuple[str, str]]:
    """`_probe_llms` for each of `urls`, all at once.

    A closed port on Windows takes its whole timeout to say so, for each address `localhost` has,
    so four presets asked one after the other held the report for twelve seconds to learn that
    nothing was running. Daemon threads rather than a pool, and a join that wakes: a Ctrl-C does not
    wait for a probe, and nothing does on the way out. A probe still asking at `PROBES_DEADLINE_S`
    is left to its daemon thread and has the row that says so."""
    return _Asking({url: functools.partial(_ask_llms, url) for url in urls}).answers(_LATE_LLMS)


def _ask_llms(url: str) -> tuple[str, str]:
    """`_probe_llms` looked up when it is called, which is where the tests replace it."""
    return _probe_llms(url)


def _ask_placement(root: str) -> tuple[list[PlacementRow], str]:
    """`_probe_placement` looked up when it is called, as `_ask_llms` is."""
    return _probe_placement(root)


_K = TypeVar("_K")
_V = TypeVar("_V")


class _Asking(Generic[_K, _V]):
    """Each of `jobs` on a daemon thread of its own, started at once, so the caller can do
    something else while they ask, and their answers by `PROBES_DEADLINE_S` from the start."""

    def __init__(self, jobs: Mapping[_K, Callable[[], _V]]) -> None:
        self._answers: dict[_K, _V] = {}
        self._raised: list[BaseException] = []
        self._threads = {
            key: threading.Thread(
                target=self._ask, args=(key, job), name="roboglot-doctor-probe", daemon=True
            )
            for key, job in jobs.items()
        }
        self._deadline = _clock() + PROBES_DEADLINE_S
        for thread in self._threads.values():
            thread.start()

    def _ask(self, key: _K, job: Callable[[], _V]) -> None:
        try:
            self._answers[key] = job()
        except BaseException as e:  # never from the real probes, which never raise
            self._raised.append(e)

    def answers(self, late: _V) -> dict[_K, _V]:
        """Every job's answer, in the order the jobs were given; one still running at the
        deadline answers `late`. What a job raised is raised here, from the first that did."""
        for thread in self._threads.values():
            while thread.is_alive() and _clock() < self._deadline:
                thread.join(0.05)
        if self._raised:
            raise self._raised[0]
        return {key: self._answers.get(key, late) for key in self._threads}


def _ollama_root(url: str) -> str:
    """The Ollama preset's address without its OpenAI-compatible `/v1`: Ollama's own API,
    `/api/ps` among it, lives at the root."""
    root = url.rstrip("/")
    return root[: -len("/v1")] if root.endswith("/v1") else root


def _probe_placement(
    root_url: str, timeout_s: float = PROBE_TIMEOUT_S
) -> tuple[list[PlacementRow], str]:
    """What an Ollama at `root_url` has loaded and where, from its own `GET /api/ps`, as
    (rows, note). The note says why there are no rows. Never raises, and nothing it finds
    touches `ok`: an LLM on the CPU is slow, not broken.

    A module attribute for the reason `_probe_llms` is one: the tests replace it."""
    import json
    import urllib.error

    url = f"{root_url.rstrip('/')}/api/ps"
    try:
        with _open(url, timeout_s) as r:
            raw = _read_reply(r, timeout_s)
    except urllib.error.HTTPError as e:
        return [], f"GET /api/ps answered HTTP {e.code}, so this may not be Ollama"
    except _TooLong:
        return [], f"GET /api/ps answered with more than {_MIB}"
    except _TooSlow:
        return [], f"GET /api/ps did not finish its reply in {timeout_s:g} s"
    except Exception:
        return [], "GET /api/ps did not answer"
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except Exception:
        return [], "GET /api/ps answered with something that is not JSON"
    llms = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(payload, dict) or not isinstance(llms, list | None):
        return [], "GET /api/ps answered with something that is not Ollama's list"

    def size_of(value: Any) -> int | None:
        # bool is an int to Python and not a size to anybody
        ok = isinstance(value, int) and not isinstance(value, bool) and value >= 0
        return int(value) if ok else None

    rows: list[PlacementRow] = []
    unplaced: list[str] = []
    for item in llms or []:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("model")
        if not isinstance(name, str) or not name:
            continue
        size, vram = size_of(item.get("size")), size_of(item.get("size_vram"))
        # an LLM of no size has no split to report, and would read as "on the CPU"
        if size and vram is not None:
            rows.append(PlacementRow(llm_id=name, size=size, size_vram=vram))
        else:
            unplaced.append(name)
    if rows:
        return rows, ""
    if unplaced:
        # Listed with no `size_vram`, or a size of 0: loaded all the same, so "nothing loaded"
        # would be false about the board, and there is no split to show for it either
        one = len(unplaced) == 1
        more = f" (+{len(unplaced) - 3})" if len(unplaced) > 3 else ""
        return [], (
            f"{', '.join(unplaced[:3])}{more} {'is' if one else 'are'} loaded, and /api/ps did "
            f"not say how much of {'it' if one else 'them'} is on the GPU"
        )
    # Ollama loads an LLM on its first request and unloads it after five idle minutes, so an
    # empty list on a healthy server is the usual answer and not a fault
    return [], "nothing loaded yet: Ollama loads an LLM on its first request"


def _urls(camera_url: str | Sequence[str] | None) -> str:
    """`--camera-url` as written: one address, or several joined by commas."""
    if camera_url is None or isinstance(camera_url, str):
        return camera_url or ""
    return ", ".join(str(url) for url in camera_url)


_URL_IN_TEXT = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s'\"<>]+")
"""A URL inside a sentence, up to the first space, quote or angle bracket. Not up to a comma,
which a password can hold: a comma that ends a URL stays in its path, which keeps it."""


def _hidden(text: str) -> str:
    """`text` with the credentials in every URL in it replaced as the run's record replaces them
    (`redacted_url`), because doctor's output is what people paste into issues. A URL that
    carries none is left exactly as it was written, rather than as `urlunsplit` would rebuild it."""

    def one(found: re.Match[str]) -> str:
        # the punctuation that ends a sentence or a clause is the sentence's, not the URL's: a
        # secret query value would otherwise take it with it when it is replaced
        url = found.group().rstrip(".,:;)]")
        tail = found.group()[len(url) :]
        shown = redacted_url(url)
        return (shown if HIDDEN in shown and HIDDEN not in url else url) + tail

    return _URL_IN_TEXT.sub(one, text)


def _without_credentials(report: ProbeReport) -> ProbeReport:
    """The probe's report with no credential from an address or a camera URL left in it: the
    table's title, every row (a camera's address comes from its backend as it was given), the
    error, which a transport can write its address into, and the advisories."""
    report.address = _hidden(report.address)
    report.error = _hidden(report.error) if report.error is not None else None
    for row in report.rows:
        row.value = _hidden(row.value)
    report.advisories = [_hidden(a) for a in report.advisories]
    return report


def probe(
    spec: str,
    static: Any,
    address: str,
    camera_url: str | Sequence[str] | None,
    token: str | None,
    rest_pose: dict[str, float] | None = None,
    *,
    name: str | None = None,
    robot_type: str | None = None,
    before_connect: Callable[[Any], None] | None = None,
    host: HostClient | None = None,
    map_address: str | None = None,
    park: Callable[[Any], Awaitable[RestResult]] | None = None,
    before_close: Callable[[Any], Awaitable[None]] | None = None,
    after_close: Callable[[], None] | None = None,
) -> ProbeReport:
    """Connect, and report what the robot itself said.

    Everything else in this file is offline and reads the *static* manifest, which describes
    a fully built robot. A real one is whatever its owner assembled, so this is the only way
    to see the difference before a run does.

    It is also the one command that moves the arm without being given a task, because a probe
    that dropped torque wherever the arm stood is how the arm fell.

    `name` is the name the robot was registered under, when it was asked for by one, and the
    body is built with it as a run builds it (`RobotSpec.robot_id`): an arm finds its
    calibration file under that id, and every line it writes names it. Without it, a probe of a
    registered arm read the calibration of the default id, whatever the arm was registered as.
    `before_connect` is handed the built body just before it connects, which is the caller's
    one chance to say something to a person holding it (`cli._doctor_warning`).

    `park` folds the body to its rest pose in place of `go_to_rest_if_any`, which is how the
    CLI puts a countdown before the fold for the hands to come away, and a press of the kill
    switch that stops it (`cli._DoctorHands`). `before_close` is awaited once the probe is done
    and before it lets go of the bus, which is the CLI's one chance to ask, over an arm whose
    close will keep its torque, whether to drop it into the person's hands instead. `after_close`
    is called last, whichever way the probe ended, and inside the event loop that ran it, which is
    where a kill switch bound to that loop has to be taken down: a press after the loop has closed
    raised in the switch's own handler and ended doctor with no report.

    `host` is the board `--host` names, when its daemon answered. Its camera joins the body
    as a run's would (`roboglot.adapters.host_camera`), and the camera rows say whether it is
    the primary view or an extra one. The body's own cameras are read only with
    `--camera-url`, as before, because only then was anybody asking about them."""
    import asyncio

    from roboglot.adapters.factory import make_adapter
    from roboglot.adapters.host_camera import HOST_CAMERA_NAME, HostCameraAdapter
    from roboglot.transport.base import DEFAULT_CAMERA_NAME, TransportError, frames_of

    # What the close on the exception path wrote, and whether `before_close` was reached first.
    # The note is the only place the person at the arm is told what became of its torque and how
    # to put it down, so an error after the connect has to carry it as the path that succeeds does.
    error_note: str | None = None
    asked = False

    async def go() -> tuple[
        Any, Any, dict[str, Any] | None, dict[str, Any], RestResult, str | None, list[str]
    ]:
        try:
            return await probed()
        finally:
            if after_close is not None:
                after_close()

    async def probed() -> tuple[
        Any, Any, dict[str, Any] | None, dict[str, Any], RestResult, str | None, list[str]
    ]:
        nonlocal error_note, asked
        parsed = parse_robot_spec(spec)
        adapter = make_adapter(
            RobotSpec(parsed.adapter, parsed.backend, name or None, robot_type=robot_type),
            address=address or None,
            camera_url=camera_url,
            token=token,
            rest_pose=rest_pose,
            host=host,
            map_address=map_address or None,
        )
        if before_connect is not None:
            before_connect(adapter)
        live = await adapter.connect()
        hosted = isinstance(adapter, HostCameraAdapter)
        # the connect attempts a body had to make again, read here and not after the close:
        # the list belongs to the connect that just happened
        retried = [str(n) for n in getattr(adapter, "connect_notes", ()) or ()]
        transport = getattr(adapter, "transport", None)
        # What the robot says about its own guarantees, rather than what Roboglot's static
        # description claims on its behalf. This is the checklist's go/no-go gate, so a
        # deadman window or an absent token has to be visible here.
        told: dict[str, Any] = dict(getattr(transport, "safety", None) or {})
        for key in ("auth_warning", "runtime_warning", "map_warning"):
            if warning := getattr(transport, key, None):
                told[key] = warning
        # what became of the robot's own map, read before the disconnect ends it
        report = getattr(transport, "mapd_report", None)
        # a robot of a family whose other bodies keep a map says None, and has no row
        if callable(report) and (mapd := report()) is not None:
            told["mapd"] = mapd
            told["places_by"] = (
                "mapd"
                if told["mapd"].get("state") == "answered"
                else "odometry"
                if getattr(transport, "spatial", None) is not None
                else None
            )
        if commit := getattr(transport, "runtime_commit", None):
            told["runtime_commit"] = commit
        closed = False
        try:
            health = await adapter.health()
            # A camera URL that nothing checks is a camera URL that fails mid-run. doctor used
            # to accept --camera-url, hand it to the transport and never ask for a frame, so a
            # typo'd or unreachable snapshot server passed here and failed at the first observe.
            camera: dict[str, Any] | None = None
            # The board's camera is the wrapper's to report, beside the body's own; the body's
            # alone is its transport's.
            cam_probe = (
                adapter.camera_health
                if isinstance(adapter, HostCameraAdapter)
                else getattr(transport, "camera_health", None)
            )
            # Only report on a camera the adapter actually reads. `camera_url` is accepted and
            # ignored by rosbridge, and gating its verdict on a frame from an unrelated path
            # fails a healthy robot.
            if (camera_url or hosted) and callable(cam_probe):
                # Asked once for the shape and again for the answer: how many cameras there
                # are decides how they are read, and the read is what fills in the sizes.
                if dict(cam_probe()).get("cameras"):
                    # One pass over every camera, because polling them one at a time is that
                    # many serial reads of the same bus for a picture each has already taken.
                    frames = await frames_of(adapter)
                    sizes = {f.name: f"{f.image.width}x{f.image.height}" for f in frames}
                    camera = dict(cam_probe())
                    camera["cameras"] = [
                        {**cam, "size": sizes.get(str(cam.get("name")))}
                        for cam in camera["cameras"]
                        # --host alone asks about the board's camera, and a body camera that
                        # nobody configured is not a failure of this probe
                        if camera_url or cam.get("name") == HOST_CAMERA_NAME
                    ]
                else:
                    # Frames arrive on a timer, so ask for one and give the capture loop a
                    # moment rather than reading memory that cannot have been filled yet.
                    frame = await adapter.get_frame()
                    for _ in range(50):
                        if frame is not None:
                            break
                        await asyncio.sleep(0.1)
                        frame = await adapter.get_frame()
                    camera = dict(cam_probe())
                    camera["frame"] = f"{frame.width}x{frame.height}" if frame is not None else None
            parked = await (park(adapter) if park is not None else go_to_rest_if_any(adapter))
            if before_close is not None:
                await before_close(adapter)
                asked = True
            # close_note is written by the disconnect, so the disconnect happens here and is
            # read from; the finally below is left as the safety net for the exception path.
            await adapter.disconnect()
            closed = True
            note = getattr(transport, "close_note", None)
            return live, health, camera, told, parked, note, retried
        finally:
            if not closed:
                await adapter.disconnect()
                error_note = getattr(transport, "close_note", None)

    try:
        live, health, camera, told, parked, note, retried = asyncio.run(go())
    except (TransportError, OSError, HostError) as e:
        where = f" at {address}" if address else ""
        failed = ProbeReport(address=address, ok=False, error=f"{spec}{where}: {e}")
        if error_note:
            failed.advisories.append(error_note)
            if before_close is not None and not asked:
                failed.advisories.append(NO_DROP_QUESTION)
        return _without_credentials(failed)

    report = ProbeReport(address=address, ok=True)
    add = report.rows.append
    add(ProbeRow("connected", "yes", "ok"))
    why = "ok" if health.ok else str(health.reason or "not ok, and it did not say why")
    add(ProbeRow("health", why, "ok" if health.ok else "fail"))
    for key, value in (health.extras or {}).items():
        add(ProbeRow(f"  {key}", "" if value is None else str(value)))
    gained = sorted(set(live.verb_names()) - set(static.verb_names()))
    lost = sorted(set(static.verb_names()) - set(live.verb_names()))
    add(ProbeRow("verbs", f"{len(live.verb_names())} of {len(static.verb_names())} described"))
    if lost:
        add(ProbeRow("  not on this robot", ", ".join(lost), "warn"))
    if gained:
        add(ProbeRow("  beyond the description", ", ".join(gained), "ok"))
    for key, value in (live.extras.get("expression_features") or {}).items():
        add(ProbeRow(f"  {key}", "yes" if value else "no", "ok" if value else "plain"))
    if told:
        # The deadman window is a free parameter and whether there is a token at all is the
        # difference between the documented setup and an open port, so both belong in front
        # of the operator at the checklist's go/no-go gate.
        # "as the robot reported it", not "this bridge": robotd reports here too now, and the
        # Open Duck's bridge is on the robot as much as robotd is
        add(ProbeRow("safety", "as the robot reported it"))
        for key in (
            "deadman_ms",
            "auth",
            "fall_detection",
            "pickup_detection",
            "move_hold",
            "getup_policy",
            "estop",
            "runtime_commit",
        ):
            if key in told:
                reported = told[key]
                # `move_hold` is why Roboglot holds a walk back for a verb still running, which the
                # Microduck's transport says beside what the robot detects, and None while it
                # holds none
                worrying = (
                    (key == "auth" and reported == "none")
                    or (
                        key in ("fall_detection", "pickup_detection", "getup_policy")
                        and reported is False
                    )
                    or (key == "move_hold" and reported is not None)
                )
                shown = "none" if reported is None else str(reported)
                add(ProbeRow(f"  {key}", shown, "warn" if worrying else "plain"))
    camera_ok = True
    several = False
    dead: list[str] = []
    host_role: str | None = None
    if camera is not None:
        cams = camera.get("cameras") or [camera]
        several = len(cams) > 1
        for cam in cams:
            # a per-camera row carries its own size; the one-camera dict carries it under
            # "frame", which is what one camera printed before any body had a second one
            size = cam.get("frame", cam.get("size"))
            name = str(cam.get("name") or DEFAULT_CAMERA_NAME)
            # only the rows a board's camera brought carry a role, so every body without one
            # prints exactly the rows it always did
            role = cam.get("role")
            if name == HOST_CAMERA_NAME and role:
                host_role = str(role)
            if size is None:
                dead.append(name)
                camera_ok = False
            add(
                ProbeRow(
                    f"camera {name}" if several or role else "camera",
                    (str(size) if size is not None else "no frame")
                    + (f" ({role})" if role else ""),
                    "ok" if size is not None else "fail",
                )
            )
            # the configured address when the camera names none of its own: a list of
            # them, as `--camera-url` can be given twice, is written out, never as a
            # Python list (`url ['opencv://0']`)
            add(ProbeRow("  url", str(cam.get("url") or _urls(camera_url))))
            if cam.get("error"):
                add(ProbeRow("  error", str(cam["error"]), "fail"))
    rest_ok = parked.reached or not parked.recorded
    if not parked.recorded:
        add(ProbeRow("rest pose", "none recorded (roboglot robot rest-pose <name>)"))
    elif parked.how == "already":
        add(ProbeRow("rest pose", "at it already", "ok"))
    elif parked.how == "arrived":
        add(ProbeRow("rest pose", "returned to it", "ok"))
    else:
        add(ProbeRow("rest pose", f"not reached: {parked.reason}", "fail"))
    # A connect that went through on a later attempt is a pass with something to say, in the
    # order it happened: the bus lost a packet, the arm connected anyway, and the joint the
    # body named is the cable to look at if it keeps happening. Not a failure: the connected
    # row above is true.
    report.advisories.extend(retried)
    if parked.note:
        # what the body has to say about the pose it parked in, which is advice and not a
        # fault: the row above is green because the arm reached the pose it can be driven to
        report.advisories.append(parked.note)
    if lost:
        report.advisories.append(
            f"a .glot that requires {lost[0]} will be refused on this robot, and one that "
            "merely allows it runs without it"
        )
    if not camera_ok:
        # naming verbs this body does not have sends the reader looking for them at the
        # moment they are trying to work out why their webcam gave nothing
        from roboglot.verbs.core import REQUIREMENTS

        blind = [
            name
            for name in live.verb_names()
            if (req := REQUIREMENTS.get(name)) is not None and req.camera
        ]
        if host_role == "extra view" and dead == [HOST_CAMERA_NAME]:
            # the body's own camera answered, so nothing goes blind: the run is shown one view
            # fewer than it would have been
            report.advisories.append(
                "the camera on the --host board gave no frame, so a run is shown the body's "
                "own camera and not the board's"
            )
        else:
            given = (
                "the camera on the --host board gave no frame"
                if dead == [HOST_CAMERA_NAME]
                else "--camera-url was given but no frame came back"
                + (f" from {', '.join(dead)}" if several else "")
            )
            report.advisories.append(
                given
                + ", so "
                + (", ".join(blind) if blind else "nothing that needs a camera")
                + " cannot see anything on this run"
            )
    if note:
        # the arm is still holding itself up, and the one place that says so is the note the
        # disconnect left behind. Not gated on the rest row: the move can report that it
        # arrived and the disconnect's own re-read still find the arm away, which is a green
        # verdict walking somebody away from an energised arm.
        report.advisories.append(note)
    for key in ("auth_warning", "runtime_warning", "map_warning"):
        if warning := told.get(key):
            report.advisories.append(str(warning))
    if told.get("places_by") == "mapd":
        report.advisories.append(
            "places are by mapd's map, and what Roboglot reads of mapd was read from a branch in "
            "no release (UNVERIFIED): watch a walk to a place before you trust one"
        )
    elif told.get("places_by") == "odometry":
        report.advisories.append(
            "places are by contact odometry, with no map: they drift as the duck walks, and "
            "are lost when it is picked up or Roboglot connects again"
        )
    if told.get("fall_detection") is False:
        report.advisories.append(
            "nothing on this robot detects a fall, so posture never becomes 'fallen' and no "
            "verb refuses because it is down. You are the fall detector: keep it on a stand "
            "and watch it."
        )
    # The other half of the same promise: a body whose software predates its pick-up detector
    # reads as never held, which is the reading that lets a walk carry on in somebody's hands.
    if told.get("pickup_detection") is False:
        report.advisories.append(
            "nothing on this robot reports when somebody is holding it, so no verb refuses "
            "because it is held, and a walk under way carries on when it is put down. Stop it "
            "before you pick it up."
        )
    # `note` and not just `rest_ok`: the rest move can report that it arrived and the
    # disconnect's own re-read still find the arm away, or fail to read it at all. That is the
    # case where the arm is left energised after a command somebody ran to be reassured, so it
    # cannot also be the case where the panel is green and the exit code is 0.
    # a map somebody named that did not answer as one fails the probe; one nobody named is
    # only reported, since mapd is in no release yet
    mapd = told.get("mapd")
    mapd_ok = not isinstance(mapd, dict) or mapd.get("state") == "answered" or not mapd.get("given")
    if isinstance(mapd, dict):
        report.rows.append(ProbeRow("mapd", mapd_words(mapd), _mapd_state(mapd)))
    report.ok = bool(health.ok) and camera_ok and rest_ok and not note and mapd_ok
    return _without_credentials(report)


def mapd_words(mapd: dict[str, Any]) -> str:
    """One line for what became of the robot's own map (`JsonRpcUnixTransport.mapd_report`)."""
    state = str(mapd.get("state") or "not probed")
    where = f" at {mapd['address']}" if mapd.get("address") else ""
    if state != "answered":
        return f"{state}{where}: {mapd['why']}" if mapd.get("why") else f"{state}{where}"
    parts = [f"answered{where}"]
    if mapd.get("keyframes") is not None:
        parts.append(f"{mapd['keyframes']} keyframes")
    parts.append(
        "knows where the robot is" if mapd.get("localized") else f"lost: {mapd.get('lost')}"
    )
    if isinstance(behind := mapd.get("behind_robotd_s"), int | float):
        parts.append(f"{behind:.2f} s behind robotd")
    return ", ".join(parts)


def _mapd_state(mapd: dict[str, Any]) -> str:
    if mapd.get("state") == "answered":
        return "ok" if mapd.get("localized") else "warn"
    if mapd.get("given"):
        return "fail"
    return "plain" if mapd.get("state") in ("absent", "not probed") else "warn"


def _microduck_api_version() -> str:
    """The `duck-ipc-proto` version Roboglot speaks, when the duck is installed to say so.

    Empty when it is not, which is what a machine with no Microduck on it should read: the
    number belongs to that robot's protocol and means nothing without it."""
    with contextlib.suppress(Exception):
        from roboglot_microduck import upstream_api as robotd

        return str(robotd.API_VERSION.name)
    return ""


def _upstreams() -> list[tuple[str, Any, str, str]]:
    """(name, module, doc, what it has or has not been run against), per installed adapter.

    Each adapter declares its own row as `UPSTREAMS`, because the list of what an adapter
    reads from upstream belongs to that adapter rather than to a table in the core that has
    to be edited whenever somebody publishes one. An adapter that declares none contributes
    none, which is what a body with no upstream to cite looks like.

    Imported inside this function rather than at module scope because doctor must not pull in
    an SDK to answer a question about it."""
    from roboglot.adapters.factory import _module, adapter_names, is_installed

    rows: list[tuple[str, Any, str, str]] = []
    for name in adapter_names():
        if not is_installed(name):
            continue
        with contextlib.suppress(Exception):
            rows.extend(tuple(row) for row in getattr(_module(name), "UPSTREAMS", ()))
    return rows


# ── the board --host names, over the network ────────────────────────────────────────────

_host_client = HostClient
"""How doctor reaches the board, and a module attribute for the reason `_probe_llms` is one:
a test replaces it. It must never become a default argument, which would bind at import and
ignore the monkeypatch."""

_GPU_BUSY = re.compile(r"GR3D_FREQ\s+(\d+)%")
"""The GPU's load in a `tegrastats` line. Orin prints `GR3D_FREQ 0%@[305]`, the clock after the
`@`, and older boards `GR3D_FREQ 0%@1300`; the percentage comes first in both."""

# The L4T table and its parser live in roboglot/boards/l4t.py, which `roboglot onboard` reads on
# the board itself. doctor reads them through the names it always had, and so does every test
# that holds doctor to them: tests/test_docs.py holds the Jetson page's table to
# `_JETPACK_FOR_L4T`.
_JETPACK_FOR_L4T = _l4t.JETPACK_FOR_L4T
_parse_l4t = _l4t.parse_l4t
_jetpack_for = _l4t.jetpack_for


def _kb_fields(text: str, wanted: Sequence[str]) -> dict[str, int]:
    """`MemTotal:  7650336 kB` into bytes, for the names asked for."""
    out: dict[str, int] = {}
    for line in text.splitlines():
        name, _, rest = line.partition(":")
        if name.strip() in wanted:
            with contextlib.suppress(ValueError, IndexError):
                out[name.strip()] = int(rest.split()[0]) * 1024
    return out


def _swaps(text: str) -> tuple[int, list[str]]:
    """`/proc/swaps` as (total bytes, device names), its sizes being in kB like `/proc/meminfo`."""
    total = 0
    devices: list[str] = []
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 3:
            continue
        devices.append(parts[0])
        with contextlib.suppress(ValueError):
            total += int(parts[2]) * 1024
    return total, devices


def _power_mode(text: str | None) -> str | None:
    """The mode name out of `nvpmodel -q`, whose answer is two labelled lines and a number."""
    for line in (text or "").splitlines():
        if "NV Power Mode" in line:
            # partition, not split: a line carrying the label and no colon is malformed
            # input, and this block's whole contract is that none of it may raise
            return line.partition(":")[2].strip() or None
    return None


def _gpu_busy(line: str | None) -> int | None:
    """`GR3D_FREQ 0%@[305]` in a `tegrastats` line as 0, or None where the line has no load."""
    match = _GPU_BUSY.search(line or "")
    return int(match.group(1)) if match else None


def _jetson_from_dump(board: HostBoard) -> JetsonReport | None:
    """What the board the daemon runs on is, from its `/board` dump, or None where it is not a
    Tegra.

    Two ways in, the two the daemon's own `capabilities.tegra` asks. The device tree is the
    kernel's and names the board on any Tegra, and `/etc/nv_tegra_release` is a file in the
    board's root filesystem that also carries the L4T release. A daemon in a container may see
    neither: `/proc/device-tree` points into `/sys/firmware`, which Docker masks unless the
    container is privileged or started with `--security-opt systempaths=unconfined`, and a plain
    image has no release file.

    The daemon parses nothing and strips the device tree's NUL terminators; they are stripped
    again here, because a NUL reaching a Rich cell is not something anyone should have to debug
    and a daemon of another version may not. Every file may be None and every command may have
    failed, and none of that may raise: doctor is what people run when something is already
    wrong, which is the worst possible place to add a new way to crash."""

    def text(path: str) -> str | None:
        value = board.files.get(path)
        return value.replace("\x00", "") if isinstance(value, str) else None

    compatible = text("/proc/device-tree/compatible") or ""
    release = text("/etc/nv_tegra_release")
    if "nvidia,tegra" not in compatible and release is None:
        return None

    l4t = _parse_l4t(release) if release is not None else None
    mem = _kb_fields(text("/proc/meminfo") or "", ("MemTotal", "MemAvailable"))
    swap_text = text("/proc/swaps")
    swap_total: int | None = None
    swap_devices: list[str] = []
    if swap_text is not None:
        swap_total, swap_devices = _swaps(swap_text)
    line = (board.commands.get("tegrastats") or "").replace("\x00", "").strip() or None
    return JetsonReport(
        board=(text("/proc/device-tree/model") or "").strip() or None,
        l4t=l4t,
        release_seen=release is not None,
        jetpack=_jetpack_for(l4t) if l4t else None,
        mem_total_bytes=mem.get("MemTotal"),
        mem_available_bytes=mem.get("MemAvailable"),
        swap_total_bytes=swap_total,
        swap_devices=swap_devices,
        gpu_device=next((node for node, there in board.nodes.items() if there is True), None),
        power_mode=_power_mode(board.commands.get("nvpmodel -q")),
        gpu_busy_pct=_gpu_busy(line),
        tegrastats=line,
        errors=dict(board.errors),
    )


def refused_host(text: str, error: str) -> HostReport:
    """A board setting refused before anything was asked of it, as a report that fails.

    The value is kept to be shown only when it is a machine, which means the token was the
    trouble. A host `parse_host` refused can hold a secret written before an `@`, or an escape
    sequence, and its own refusals quote neither back, so neither does the title of this
    section nor --json."""
    try:
        parse_host(text)
    except ValueError:
        text = ""
    return HostReport(host=text, ok=False, error=error)


def _ask_host(
    host: str, token: str | None, say: Any, *, token_fix: str | None = None
) -> HostReport:
    """Everything the daemon at `host` will say, as a report that never raises.

    `/hello` first, because it is the one answer a run needs and the one this report's `ok`
    follows. `/healthz` and `/board` after it, each allowed to fail on its own: a daemon that
    answered hello and then timed out on the board dump is a daemon a run would still use."""
    try:
        client = _host_client(host, token=token, token_fix=token_fix)
    except ValueError as e:
        # a host that is no machine, or a token no header can carry: the CLI settles both
        # before collect runs, so this is a library caller, told the same way
        return refused_host(host, str(e))
    report = HostReport(host=host, address=client.address, tried=f"{client.base_url}/hello")
    say(f"asking the daemon at {client.address}")
    try:
        report.hello = client.hello()
    except HostError as e:
        report.error = str(e)
        report.unresolved = e.unresolved
        return report
    report.ok = True
    say(f"reading the health of {client.address}")
    try:
        report.healthz = client.healthz()
    except HostError as e:
        report.healthz_error = str(e)
    say(f"reading the board at {client.address}")
    try:
        report.jetson = _jetson_from_dump(client.board())
    except HostError as e:
        report.board_error = str(e)
    return report


def _undescribed(robot: str, robot_name: str | None, e: AdapterError) -> str:
    """Why `--robot` could not be described, in the adapter's own words. A registered robot
    whose stored spec this Roboglot cannot parse is named, with the fix
    (`roboglot.registry.unusable_spec`), since the spec on the report is one nobody typed. One
    that parses and still fails, an adapter whose package is missing, keeps its own words,
    which already say what to install."""
    if robot_name is None:
        return str(e)
    from roboglot.registry import unusable_spec

    try:
        parse_robot_spec(robot)
    except AdapterError:
        return unusable_spec(robot_name, robot, str(e))
    return str(e)


def collect(
    robot: str | None = None,
    *,
    address: str | None = None,
    camera_url: str | Sequence[str] | None = None,
    token: str | None = None,
    rest_pose: dict[str, float] | None = None,
    host: str | None = None,
    host_token: str | None = None,
    map_address: str | None = None,
    progress: Progress = None,
    robot_name: str | None = None,
    robot_type: str | None = None,
    before_connect: Callable[[Any], None] | None = None,
    host_token_fix: str | None = None,
    park: Callable[[Any], Awaitable[RestResult]] | None = None,
    before_close: Callable[[Any], Awaitable[None]] | None = None,
    after_close: Callable[[], None] | None = None,
) -> DoctorReport:
    """Every question doctor asks, answered as data.

    Nothing in here decides what anything looks like, which is what lets `--json` exist and
    what keeps `render` honest about where its numbers came from.

    `robot_name` is the registered name `robot` was resolved from, when it was, and the robot
    is described and built under it, as a run builds it; `before_connect`, `park`, `before_close`
    and `after_close` go to `probe`. Not `name`, which this function's own loops over providers
    and presets rebind.

    `host` is a board already settled by `roboglot.host.resolve_host` (the flag, a registered
    robot's, or `ROBOGLOT_HOST`), and `host_token` the token that goes with it. With one, the
    daemon on it is asked what it is, and the four local presets are probed on that machine
    rather than on this one. Nothing on this machine is read to find a board: no `/proc`, no
    subprocess, only the network. `host_token_fix` is what a refused token's message says to
    do, for the place the token came from (`HostChoice.token_fix`)."""

    def say(message: str) -> None:
        if progress is not None:
            progress(message)

    report = DoctorReport(
        version=__version__,
        python=platform.python_version(),
        platform=f"{platform.system()} {platform.release()} {platform.machine()}",
        api_version=_microduck_api_version(),
    )

    host = (host or "").strip() or None
    if host is not None:
        report.host = _ask_host(host, host_token, say, token_fix=host_token_fix)

    custom = os.environ.get("ROBOGLOT_BASE_URL")
    # With a board, the four presets are asked on it, as a run with --host would reach them;
    # `local` has no preset address and keeps meaning ROBOGLOT_BASE_URL. A host that is no
    # machine was already reported above, and its presets stay where they were. A daemon that
    # is down or refused still has its board's presets asked, since the LLM server can be up
    # without it; a name that found no address has none asked, since there is no machine to ask.
    board = report.host if report.host is not None and report.host.address else None
    # In the order they print: a row with its answer already, or a server still to be asked.
    slots: list[ServerRow | tuple[str, str, str, bool]] = []
    for preset, url in {**PRESETS, **({"local": custom} if custom else {})}.items():
        if not url:
            slots.append(ServerRow(preset, "", "unset", "set ROBOGLOT_BASE_URL or --base-url"))
            continue
        on_board = False
        if board is not None and preset != "local":
            with contextlib.suppress(ValueError):
                url, on_board = on_host(url, board.host), True
        # ROBOGLOT_BASE_URL is a URL a person typed, so it can carry a password in its userinfo,
        # and this screen and its spinner are what people paste into issues
        shown = redacted_url(url)
        if board is not None and on_board and board.unresolved:
            skipped = ServerRow(
                preset, shown, "skipped", f"not asked: {host_of(board.host)} has no address"
            )
            slots.append(skipped)
            board.servers.append(skipped)
            continue
        say(f"probing {preset} at {shown}")
        slots.append((preset, url, shown, on_board))
    # Started here and read after the import checks below, which used to run first and add
    # their own seconds to the ones the probes spend waiting on ports nothing listens on.
    to_ask = [slot[1] for slot in slots if isinstance(slot, tuple)]
    asking = _Asking({url: functools.partial(_ask_llms, url) for url in to_ask})

    say("checking the core packages")
    for name, module in CORE_MODULES:
        version = _installed(module)
        report.core.append(Check(name, version is not None, version or "missing"))
    report.bundled_tasks = len(list_bundled_tasks())

    say("checking the providers")
    # ROBOGLOT_LLM names one vendor and, after the colon, at most one LLM of that vendor's, so
    # it pins exactly one row of this table and says nothing about the other sixteen. Read once
    # here rather than once per row: the variable it replaced was a bare LLM id with no
    # vendor on it, so every row had to guess whether it was the one being talked about.
    env_llm = os.environ.get("ROBOGLOT_LLM", "").strip()
    env_vendor, env_llm_id = "", None
    if env_llm:
        try:
            env_vendor, env_llm_id = parse_llm(env_llm, source="ROBOGLOT_LLM")
        except ProviderError as e:
            # A vendor nobody publishes has no row to be shown on, and the run it is about to
            # refuse is the thing a reader came here to understand, so it is kept and printed
            # under the table rather than dropped.
            report.llm_env_error = str(e)
    for name in PROVIDER_NAMES:
        if name == "fake":
            report.providers.append(
                ProviderRow("fake", "built-in", "built-in", "", "", True, "scripted")
            )
            continue
        key = os.environ.get(KEY_ENV[name], "")
        # The provider reads its second name when the first is empty, so doctor does too: a row
        # saying COHERE_API_KEY unset beside an exported CO_API_KEY was wrong about the run.
        key_from = ""
        if not key and name in KEY_FALLBACKS:
            key = os.environ.get(KEY_FALLBACKS[name], "")
            key_from = KEY_FALLBACKS[name] if key else ""
        # What this provider would actually be given, not what the table used to guess. The
        # vendor ROBOGLOT_LLM names can still be handed an LLM it does not list, and doctor is
        # where a reader should find that out rather than three commands later.
        wanted = env_llm_id if name == env_vendor else None
        refused = ""
        try:
            llm_id = resolve_llm_id(name, wanted, source="ROBOGLOT_LLM") or "auto (first served)"
        except ProviderError:
            llm_id = DEFAULT_LLMS.get(name) or "auto (first served)"
            refused = env_llm
        report.providers.append(
            ProviderRow(
                name=name,
                extra=f"roboglot[{EXTRA_FOR[name]}]",
                version=_installed(SDK_FOR[name]),
                key=_mask(key) if key else "",
                key_env=KEY_ENV[name],
                key_optional=name in LOCAL_NAMES,
                llm_id=llm_id,
                pinned=name == env_vendor and not refused,
                refused_llm=refused,
                key_from=key_from,
            )
        )

    # Asked for whether or not anybody uses one, because "which decision LLM could run here?"
    # is a question a reader of this screen should be able to answer without starting a task,
    # and the answer is almost always "none of them yet". Every row Roboglot names is printed on
    # a machine with none of them installed: this is a catalogue and not an inventory, so the
    # missing rows are the informative ones. Nothing here is probed over the network -- the
    # providers table does not knock on OpenAI either, and these servers do not all publish a
    # listing endpoint to knock on.
    from roboglot.agent.decision.catalogue import IN_PROCESS, SYSTEM_ONE
    from roboglot.agent.decision.factory import (
        find_preset,
        preset_names,
        resolve_decision_llm,
        resolve_decision_url,
    )

    for decision_name in preset_names():
        spec = find_preset(decision_name)
        decision_key = os.environ.get(spec.key_env, "") if spec.key_env else ""
        where = resolve_decision_url(spec) or ""
        if spec.backend == IN_PROCESS:
            # It has no address at all, and a blank cell there reads as an address somebody
            # forgot to set rather than one that was never wanted.
            where = "in this process"
        elif where:
            # ROBOGLOT_DECISION_URL is a URL a person typed, so it can carry a password in its
            # userinfo the way --base-url can, and this screen is pasted into issues.
            where = redacted_url(where)
        elif spec.backend == SYSTEM_ONE and spec.key_env is None:
            # `local` is the row that exists to be told an address, and `make_decision_llm`
            # refuses it without one. The same sentence here as in that refusal, so the fix a
            # reader copies off this screen is the one the run would have asked them for.
            where = "unset: --decision-url or ROBOGLOT_DECISION_URL"
        report.steppers.append(
            ProviderRow(
                name=spec.name,
                extra=f"roboglot[{spec.extra}]" if spec.extra else "",
                # A plugin was found through its own metadata, so it is installed by
                # definition and has no import to probe: "?" is this file's word for "here,
                # and it did not say which version".
                version=_installed(spec.sdk) if spec.sdk else "?",
                key=_mask(decision_key) if decision_key else "",
                key_env=spec.key_env or "",
                # A server you run yourself wants no key and is sent NO_KEY, so an empty cell
                # there is not a machine with something missing.
                key_optional=spec.key_env is None,
                llm_id=resolve_decision_llm(spec) or "auto (the server names it)",
                url=where,
                pinned=bool(spec.llm_env and os.environ.get(spec.llm_env)),
            )
        )

    answers = asking.answers(_LATE_LLMS)
    for slot in slots:
        if isinstance(slot, ServerRow):
            report.servers.append(slot)
            continue
        preset, url, shown, on_board = slot
        state, detail = answers[url]
        row = ServerRow(preset, shown, state, detail)
        if preset == "ollama" and state == "up":
            root = _ollama_root(url)
            say(f"asking ollama at {redacted_url(root)} where its LLMs are")
            # by the deadline the probes above keep, for the reason they keep one
            late: tuple[list[PlacementRow], str] = (
                [],
                f"GET /api/ps did not finish answering in {PROBES_DEADLINE_S:g} s",
            )
            placed = _Asking({root: functools.partial(_ask_placement, root)}).answers(late)
            row.placement, row.placement_note = placed[root]
        report.servers.append(row)
        if board is not None and on_board:
            board.servers.append(row)

    report.adapters = list_adapters()

    if robot is not None:
        say(f"describing {robot}")
        try:
            parsed = parse_robot_spec(robot)
            manifest = describe(
                RobotSpec(parsed.adapter, parsed.backend, robot_name or None, robot_type=robot_type)
            )
        except AdapterError as e:
            report.robot = RobotReport(spec=robot, error=_undescribed(robot, robot_name, e))
        else:
            report.robot = RobotReport(
                spec=robot,
                summary=manifest.summary(),
                verbs=[
                    VerbRow(
                        name=v.name,
                        core=bool(v.core),
                        safety=v.safety_class,
                        preconditions=list(manifest.preconditions.get(v.name, [])),
                    )
                    for v in manifest.verbs
                ],
            )
            # a real body with no address is never guessed at
            if address:
                shown = f"{robot_name} ({robot})" if robot_name else robot
                # the address as the probe's report shows it, with no credential in it
                say(f"connecting to {shown}" + (f" at {_hidden(address)}" if address else ""))
                # the board's camera joins the body as it would a run's, but only a board that
                # answered: one that did not has already failed this report with a row of its
                # own, and asking it again from inside the probe would only say so twice
                host_camera_from = (
                    _host_client(host, token=host_token)
                    if host is not None and report.host is not None and report.host.ok
                    else None
                )
                report.robot.probe = probe(
                    robot,
                    manifest,
                    address or "",
                    camera_url,
                    token,
                    rest_pose,
                    name=robot_name,
                    robot_type=robot_type,
                    before_connect=before_connect,
                    host=host_camera_from,
                    map_address=map_address,
                    park=park,
                    before_close=before_close,
                    after_close=after_close,
                )

    # An adapter that has backends worth probing on this machine says so itself (`doctor_rows`),
    # which none Roboglot publishes does today. That knowledge belongs to the adapter rather than
    # to a table here that would have to be edited whenever somebody publishes an adapter, and
    # each row names its own `adapter:backend`, since the table is no longer one robot's.
    for name in adapter_names():
        if not is_installed(name):
            continue
        with contextlib.suppress(Exception):
            rows = getattr(_adapter_module(name), "doctor_rows", None)
            if callable(rows):
                report.transports.extend(rows())

    say("checking the optional extras")
    for label, (module, extra) in EXTRAS.items():
        version = _installed(module)
        report.extras.append(
            Check(label, version is not None, version or f"not installed ({extra})")
        )

    for name, api, doc, never in _upstreams():
        unverified = api.refs_by_status("UNVERIFIED")
        for ref in unverified:
            report.assumptions.append(Assumption(name, ref.name, ref.note))
        report.pins.append(
            PinRow(
                upstream=name,
                pin=api.PIN[:7],
                extra_pin=getattr(api, "POLICIES_PIN", "")[:7],
                read_on=api.READ_ON,
                verified=len(api.refs_by_status("VERIFIED")),
                unverified=len(unverified),
                never_run=never,
                doc=doc,
            )
        )
    return report


# ── showing ─────────────────────────────────────────────────────────────────────────────

_STATE_STYLE = {"ok": "ok", "up": "ok", "warn": "warn", "http": "warn", "fail": "fail"}


def _section(console: Console, title: str) -> None:
    """A rule rather than a table title. There are ten sections here and they used to arrive
    as ten stacked tables with nothing between them, which read as one long table."""
    console.print()
    console.print(Rule(Text(title, style=ui.STYLES["key"]), align="left", style=ui.STYLES["rule"]))


def _checks(checks: list[Check], *, missing_is_fine: bool = False) -> Any:
    """A yes-or-no list. A missing extra is a choice; a missing core package is a fault."""

    def build(g: ui.Glyphs) -> Any:
        rows: list[tuple[str, Any]] = []
        for check in checks:
            if check.ok:
                mark, style = g.ok, ui.STYLES["ok"]
            elif missing_is_fine:
                mark, style = g.note, ui.STYLES["muted"]
            else:
                mark, style = g.fail, ui.STYLES["fail"]
            rows.append((f"{mark} {check.name}", Text(check.detail, style=style)))
        return ui.kv_grid(rows, key_style="")

    return ui.Deferred(build)


def _gib(n: int) -> str:
    return f"{n / (1024**3):.1f} GiB"


def _flat(text: Any, limit: int = 500) -> str:
    """A string the board sent, made safe for one cell of this terminal: control characters,
    an escape sequence among them, turned to spaces, and a long one cut short. The daemon is
    another machine on the network, and what it says is printed on this one."""
    flat = "".join(c if c.isprintable() else " " for c in str(text))
    return flat if len(flat) <= limit else flat[: limit - 3] + "..."


_Row = Callable[..., None]
"""`row(key, value, style="muted", mark="")`: one line of a grid, as the grids below add it."""


def _jetson_rows(jetson: JetsonReport, row: _Row, g: ui.Glyphs) -> None:
    """The board the daemon runs on, for somebody about to put an LLM on it.

    Warnings only where a person would have to do something about it: swap that cannot hold an
    LLM. The GPU node is a note rather than a warning, because Roboglot reading the board
    without one is not a fault. Every value here came from the board, so every one is flattened
    before it reaches a cell."""

    def why(key: str) -> str:
        reason = jetson.errors.get(key)
        return f" ({_flat(reason, 120)})" if reason else ""

    if jetson.board:
        row("board", _flat(jetson.board))
    else:
        model = "/proc/device-tree/model"
        row("board", f"unknown: the daemon could not read {model}{why(model)}")
    if jetson.l4t:
        named = f" (JetPack {jetson.jetpack})" if jetson.jetpack else ""
        row("L4T", f"{jetson.l4t}{named}")
    elif jetson.release_seen:
        row(
            "L4T",
            "/etc/nv_tegra_release is on the board and its first line is not one this build knows",
        )
    else:
        row(
            "L4T",
            "unknown: the daemon could not read /etc/nv_tegra_release"
            f"{why('/etc/nv_tegra_release')}. A daemon in a container usually cannot",
        )

    if jetson.mem_total_bytes is not None:
        free = (
            f", {_gib(jetson.mem_available_bytes)} available"
            if jetson.mem_available_bytes is not None
            else ""
        )
        row("memory", f"{_gib(jetson.mem_total_bytes)}{free}, shared with the GPU")

    if jetson.swap_total_bytes is not None:
        if jetson.swap_total_bytes == 0:
            row(
                "swap",
                "none. An LLM that does not fit in memory cannot load, and a swapfile on the "
                "board's NVMe is what lets a bigger one in",
                "warn",
                g.warn,
            )
        elif jetson.swap_only_zram:
            row(
                "swap",
                f"{_gib(jetson.swap_total_bytes)}, all zram: it compresses RAM rather than "
                "adding any (docs/guides/jetson.md)",
                "warn",
                g.warn,
            )
        else:
            devices = _flat(", ".join(jetson.swap_devices), 200)
            row("swap", f"{_gib(jetson.swap_total_bytes)} on {devices}")

    if jetson.gpu_device:
        row("GPU device", _flat(jetson.gpu_device, 120))
    else:
        row(
            "GPU device",
            "none the daemon could see. The LLM server and the detector want one, and "
            "Roboglot never asks for one",
        )

    if jetson.power_mode:
        row("power mode", f"{_flat(jetson.power_mode, 120)} (nvpmodel -q)")
    elif "nvpmodel -q" in jetson.errors:
        row("power mode", f"unknown: nvpmodel -q gave nothing{why('nvpmodel -q')}")

    if jetson.gpu_busy_pct is not None:
        row("GPU busy", f"{jetson.gpu_busy_pct}% (GR3D_FREQ in tegrastats)")
    elif jetson.tegrastats is None and "tegrastats" in jetson.errors:
        row("GPU busy", f"unknown: tegrastats gave nothing{why('tegrastats')}")
    if jetson.tegrastats:
        row("tegrastats", _flat(jetson.tegrastats))


def _host_title(host: HostReport) -> str:
    """Jetson in the title only when the daemon or the board's files say so: the daemon runs on
    a laptop for a test, and a section calling that a Jetson would be the one lie on the page.
    The word stays out of the other titles altogether, so a reader scanning for it finds only a
    board that is one; the board row below the title says what the machine is not."""
    where = host.address or host.host
    if not where:
        return "host"
    if host.hello is None:
        return f"host at {where}"
    if host.is_tegra:
        return f"Jetson at {where} (the board the daemon runs on)"
    return f"host at {where} (the machine the daemon runs on)"


def _host_grid(host: HostReport) -> Any:
    """The board `--host` names, once its daemon has answered: what the daemon is, the camera
    and the detector it offers, whether it is well, and then the board itself.

    A missing camera or detector is a note, not a warning: `--camera none` and `--no-detect`
    are choices, and the health row is where one that was asked for and failed shows up."""

    def build(g: ui.Glyphs) -> Any:
        rows: list[tuple[str, Any]] = []

        def row(key: str, value: str, style: str = "muted", mark: str = "") -> None:
            rows.append((f"{mark or g.note} {key}", Text(value, style=ui.STYLES[style])))

        hello = host.hello
        if hello is not None:
            row(
                "daemon",
                f"{PROTOCOL} {_flat(hello.daemon_version, 40)}, protocol "
                f"{hello.protocol_version}, on {_flat(hello.hostname, 120)}",
                "ok",
                g.ok,
            )
            if hello.has_camera:
                size = hello.camera_size
                said = [f"{size[0]}x{size[1]}" if size else "a camera"]
                if hello.camera_fps is not None:
                    said.append(f"at {hello.camera_fps:g} fps")
                source = (hello.camera or {}).get("source")
                if isinstance(source, str) and source:
                    said.append(f"from {_flat(source, 120)}")
                fov = hello.camera_fov_deg
                lens = (
                    f"field of view {fov:g} degrees"
                    if fov is not None
                    else "field of view not given: start the daemon with --fov-deg for bearings"
                )
                row("camera", f"{' '.join(said)}, {lens}", "ok", g.ok)
            else:
                row("camera", f"none: {_flat(hello.camera_error or 'the daemon did not say why')}")
            label = hello.label()
            if label and (hello.detect or {}).get("device") == "cpu":
                row(
                    "detector",
                    f"{_flat(label, 120)}: the board's torch sees no CUDA "
                    "(bridge/jetson/README.md)",
                    "warn",
                    g.warn,
                )
            elif label:
                row("detector", _flat(label, 120), "ok", g.ok)
            else:
                row(
                    "detector",
                    f"none: {_flat(hello.detect_error or 'the daemon did not say why')}",
                )
        if host.healthz_error:
            row("health", f"could not be read: {_flat(host.healthz_error)}", "warn", g.warn)
        elif host.healthz is not None:
            if host.healthz.get("ok") is True:
                row("health", "ok", "ok", g.ok)
            else:
                reason = host.healthz.get("reason")
                row(
                    "health",
                    _flat(reason)
                    if isinstance(reason, str) and reason
                    else "not ok, and it did not say why",
                    "warn",
                    g.warn,
                )
        if host.jetson is not None:
            _jetson_rows(host.jetson, row, g)
        elif host.board_error:
            row("board", f"could not be read: {_flat(host.board_error)}", "warn", g.warn)
        elif host.is_tegra:
            row("board", "a Tegra by the daemon's account, and none of the files it sent says so")
        else:
            row("board", "not a Jetson: no Tegra in its device tree, no /etc/nv_tegra_release")
        return ui.kv_grid(rows, key_style="")

    return ui.Deferred(build)


def _placement_grid(server: ServerRow, *, tegra: bool) -> Any:
    """Where Ollama put each LLM it has loaded. An LLM on a Jetson's CPU is the pitfall the
    Jetson page names, so that one says what to do about it; on any other machine an LLM on
    the CPU may be all there is, and it is a note."""

    def build(g: ui.Glyphs) -> Any:
        rows: list[tuple[str, Any]] = []

        def row(key: str, value: str, style: str = "muted", mark: str = "") -> None:
            rows.append((f"{mark or g.note} {key}", Text(value, style=ui.STYLES[style])))

        for llm_id in server.placement or []:
            name = _flat(llm_id.llm_id, 80)
            if llm_id.where == "gpu":
                row(name, f"all on the GPU, {_gib(llm_id.size)}", "ok", g.ok)
            elif llm_id.where == "partial":
                row(name, f"{llm_id.gpu_pct}% on the GPU, the rest on the CPU", "warn", g.warn)
            elif tegra:
                row(
                    name,
                    "on the CPU: the generic arm64 build of Ollama has no Tegra CUDA, and the "
                    "official installer picks the JetPack build (docs/guides/jetson.md)",
                    "warn",
                    g.warn,
                )
            else:
                row(name, "on the CPU: this Ollama put none of it on a GPU")
        if not rows:
            # flattened, because the note can name the LLMs another machine's Ollama sent
            row("loaded", _flat(server.placement_note or "nothing"))
        return ui.kv_grid(rows, key_style="")

    return ui.Deferred(build)


def _providers_table(report: DoctorReport) -> Any:
    table = ui.table()
    table.add_column("provider", style=ui.STYLES["key"], no_wrap=True)
    table.add_column("extra")
    table.add_column("key")
    # folded, not elided: an LLM id with an ellipsis through it cannot be pasted into --llm
    table.add_column("default LLM", overflow="fold")
    for row in report.providers:
        if row.name == "fake":
            table.add_row(
                Text("fake"), Text("built-in", style=ui.STYLES["ok"]), Text("-"), Text(row.llm_id)
            )
            continue
        extra = (
            Text(str(row.version), style=ui.STYLES["ok"])
            if row.version
            else Text.assemble(("missing ", ui.STYLES["warn"]), (f"({row.extra})", ""))
        )
        key: Any
        if row.key and row.key_from:
            key = Text.assemble(
                (row.key, ui.STYLES["ok"]), (f" from {row.key_from}", ui.STYLES["muted"])
            )
        elif row.key:
            key = ui.plain(row.key, style=ui.STYLES["ok"])
        elif row.key_optional:
            key = Text("optional", style=ui.STYLES["muted"])
        else:
            key = Text(f"{row.key_env} unset", style=ui.STYLES["warn"])
        if row.refused_llm:
            llm_id = Text.assemble(
                (f"ROBOGLOT_LLM={row.refused_llm}", ui.STYLES["warn"]),
                (f", which {row.name} does not list", ui.STYLES["warn"]),
            )
        else:
            llm_id = Text(row.llm_id, style=ui.STYLES["ok"] if row.pinned else "")
        table.add_row(Text(row.name), extra, key, llm_id)
    return table


def _steppers_table(report: DoctorReport) -> Any:
    """The providers table's shape, so a reader recognises it, and its own section, so nobody
    reads a decision LLM as something `--llm` takes.

    One row per decision LLM Roboglot names, plus whatever a plugin added, installed or not: the
    table is here to show what this machine would have to install, so a row for something that
    is not here yet is the one worth printing. The url column is the difference from the
    providers table, because most of these are a server somebody runs themselves."""
    table = ui.table()
    table.add_column("decision llm", style=ui.STYLES["key"], no_wrap=True)
    # unwrapped, with the hint on a line of its own: a hint is pasted as a url is, so it is never
    # cut, and `(roboglot[decision])` is wider than the column a hundred columns would leave it
    table.add_column("extra", no_wrap=True)
    table.add_column("key")
    table.add_column("LLM", overflow="fold")
    # folded, not elided: a url with an ellipsis through it cannot be pasted into --decision-url
    table.add_column("url", overflow="fold")
    for row in report.steppers:
        extra = (
            Text(str(row.version), style=ui.STYLES["ok"])
            if row.version
            else Text.assemble(("missing", ui.STYLES["warn"]), "\n", (f"({row.extra})", ""))
        )
        key: Any
        if row.key:
            key = ui.plain(row.key, style=ui.STYLES["ok"])
        elif row.key_optional:
            key = Text("none needed", style=ui.STYLES["muted"])
        else:
            key = Text(f"{row.key_env} unset", style=ui.STYLES["muted"])
        table.add_row(
            Text(row.name),
            extra,
            key,
            Text(row.llm_id, style=ui.STYLES["ok"] if row.pinned else ""),
            # muted wherever the cell is a sentence about an address rather than an address,
            # which is how the servers table prints a preset it was never given one for
            Text(row.url, style="" if row.url.startswith("http") else ui.STYLES["muted"]),
        )
    return table


def _servers_table(report: DoctorReport) -> Any:
    table = ui.table()
    table.add_column("preset", style=ui.STYLES["key"], no_wrap=True)
    table.add_column("base url")
    table.add_column("status")
    for row in report.servers:
        if row.state == "unset":
            table.add_row(Text(row.preset), Text(row.detail, style=ui.STYLES["muted"]), Text(""))
            continue
        if row.state == "up":
            # the detail is the LLM ids, which are the answer; the word is the good news
            status = Text("up", style=ui.STYLES["ok"])
            status.append(f" {row.detail}", style=ui.STYLES["muted"])
        else:
            # the detail already says it ("not running", "HTTP 500"), so the word would be
            # the same thing twice
            status = Text(row.detail, style=ui.STYLES[_STATE_STYLE.get(row.state, "muted")])
        table.add_row(Text(row.preset), Text(row.url), status)
    return table


def _verbs_table(robot: RobotReport) -> Any:
    table = ui.table(f"{robot.spec}: {robot.summary}")
    table.add_column("verb", style=ui.STYLES["key"], no_wrap=True)
    table.add_column("core")
    table.add_column("safety")
    table.add_column("preconditions")
    for verb in robot.verbs:
        table.add_row(
            Text(verb.name),
            Text("core" if verb.core else ""),
            Text(
                verb.safety, style=ui.STYLES["ok"] if verb.safety == "safe" else ui.STYLES["warn"]
            ),
            Text(", ".join(verb.preconditions), style=ui.STYLES["muted"]),
        )
    return table


def _probe_table(probe_report: ProbeReport) -> Any:
    # a probe of a body that keeps no address has no place to name
    where = f"at {probe_report.address}: " if probe_report.address else ""
    table = ui.table(f"{where}what the robot itself reported")
    table.add_column("what", style=ui.STYLES["key"], no_wrap=True)
    table.add_column("value")
    for row in probe_report.rows:
        style = ui.STYLES.get(_STATE_STYLE.get(row.state, ""), "")
        table.add_row(Text(row.what), Text(row.value, style=style))
    return table


def _assumptions_table(report: DoctorReport) -> Any:
    """One table for eight upstreams, sectioned. It was eight tables and eight dim footers,
    and a screen of them buried the one line anybody had come to read."""
    table = ui.table()
    table.add_column("upstream", style=ui.STYLES["key"], no_wrap=True)
    # `overflow="fold"` on both: a note that quotes a long URL, or a ref's name on a narrow
    # console, is broken across lines rather than cut with an ellipsis, which is a
    # character a cp1252 console cannot carry and a loss of the one thing the row was there
    # to say.
    table.add_column("what", overflow="fold")
    table.add_column("note", ratio=2, overflow="fold")
    last = ""
    for item in report.assumptions:
        if last and item.upstream != last:
            table.add_section()
        table.add_row(
            Text(item.upstream if item.upstream != last else ""),
            Text(item.what),
            ui.plain(item.note, style=ui.STYLES["muted"]),
        )
        last = item.upstream
    return table


def _read_more(report: DoctorReport) -> Any:
    """Where to read about each upstream.

    A line rather than a column: eight table titles used to carry these paths, and a title
    is full width. Folded into a sixth column of the pins table, a path loses its tail on
    any terminal under 120, and a path you cannot copy is not a path."""
    parts = ["read more:", *(f"{pin.upstream} {pin.doc}" for pin in report.pins)]
    return ui.Deferred(lambda g: ui.joined(parts, g))


def _pins_table(report: DoctorReport) -> Any:
    table = ui.table("where each upstream was read, and what it has and has not been run against")
    table.add_column("upstream", style=ui.STYLES["key"], no_wrap=True)
    table.add_column("pinned at", no_wrap=True)
    table.add_column("read on", no_wrap=True)
    table.add_column("refs", no_wrap=True)
    table.add_column("never run against")
    for pin in report.pins:
        refs = Text.assemble(
            (f"{pin.verified} verified", ui.STYLES["ok"]),
            ", ",
            (f"{pin.unverified} not", ui.STYLES["warn"] if pin.unverified else ui.STYLES["muted"]),
        )
        pinned = Text(pin.pin)
        if pin.extra_pin:
            pinned.append(f", policies {pin.extra_pin}", style=ui.STYLES["muted"])
        table.add_row(
            Text(pin.upstream),
            pinned,
            Text(pin.read_on, style=ui.STYLES["muted"]),
            refs,
            ui.plain(pin.never_run, style=ui.STYLES["muted"]),
        )
    return table


def _transports_table(report: DoctorReport) -> Any:
    table = ui.table()
    table.add_column("name", style=ui.STYLES["key"], no_wrap=True)
    table.add_column("status")
    table.add_column("notes")
    for row in report.transports:
        table.add_row(
            Text(row.name),
            _status_cell(row.status),
            ui.plain(row.note, style=ui.STYLES["ok"] if row.found else ui.STYLES["muted"]),
        )
    return table


def _status_cell(status: str) -> Any:
    """The registry's status string, spelled for whichever console draws the table."""
    return ui.plain(status)


def verdict(report: DoctorReport) -> Any:
    """The line this command exists to produce and never printed: the exit code was the only
    summary it had, and nobody reads an exit code off a screen."""
    if report.ok:
        # Since every adapter became its own package a green machine can have no robot at all,
        # and this line is what `README.md` sends a new reader to read: saying a robot runs on a
        # machine where `roboglot run` refuses for want of one is the opposite of what this
        # command is for.
        installed = [row["name"] for row in report.adapters if row.get("adapter_installed")]
        if not installed:
            reason = (
                "Roboglot itself runs here, and no robot adapter is installed: "
                'uv pip install "roboglot[microduck]" for one'
            )
        else:
            reason = f"the scripted pilot runs here, on {', '.join(installed)}"
    elif report.missing_core:
        reason = f"a core package is missing: {', '.join(report.missing_core)}"
    elif report.robot and report.robot.error:
        reason = report.robot.error
    elif report.robot and report.robot.probe and report.robot.probe.error:
        reason = report.robot.probe.error
    elif report.robot and report.robot.probe and not report.robot.probe.ok:
        # the probe fails on health OR on a camera that sent no frame, and blaming health
        # for a camera sends the reader to the wrong end of the robot
        bad = [r for r in report.robot.probe.rows if r.state == "fail"]
        reason = f"{bad[0].what}: {bad[0].value}" if bad else "the robot did not report healthy"
    elif report.host is not None and not report.host.ok:
        reason = report.host.error or f"{report.host.host} did not answer"
    else:
        reason = "the robot did not report healthy"
    # the described row is not an adapter: its backends are bodies nothing drives, counted
    # apart so that "10 adapters" never includes them
    adapters = [a for a in report.adapters if a.get("kind", "adapter") == "adapter"]
    described = sum(len(a["backends"]) for a in report.adapters if a.get("kind") == "described")
    counters = [
        f"{sum(1 for e in report.extras if e.ok)}/{len(report.extras)} extras",
        # a fraction, not a total: since every adapter became its own package the number that
        # matters is how many are here, and "10 adapters" beside a table of ten `not
        # installed` rows reads as ten you have
        f"{sum(1 for a in adapters if a.get('adapter_installed'))}/{len(adapters)} adapters "
        "installed",
        f"{described} described bodies",
        f"{report.bundled_tasks} bundled tasks",
        f"{len(report.assumptions)} unverified assumptions",
    ]
    if report.cloud_keys:
        counters.append(f"no key for {', '.join(report.cloud_keys)}")
    return ui.verdict("success" if report.ok else "failure", reason, counters=counters)


def render(console: Console, report: DoctorReport) -> None:
    """The report as a person reads it: sections in the order you would work through them,
    ending with the one line the exit code used to stand in for."""
    console.print(
        Text.assemble(
            (f"Roboglot {report.version}", ui.STYLES["key"]),
            (f"  Python {report.python}  {report.platform}", ui.STYLES["muted"]),
            # the Microduck's protocol, which a machine without that adapter has no number for
            (
                f"  duck-ipc-proto API v{report.api_version}" if report.api_version else "",
                ui.STYLES["muted"],
            ),
        )
    )

    if report.host is not None:
        _section(console, _host_title(report.host))
        if report.host.ok:
            console.print(_host_grid(report.host))
        else:
            tried = f"tried GET {report.host.tried}" if report.host.tried else None
            console.print(
                ui.fail_line(report.host.error or "the host did not answer", hint=tried),
                soft_wrap=True,
            )

    _section(console, "core")
    console.print(_checks(report.core))
    console.print(Text(f"  {report.bundled_tasks} bundled tasks", style=ui.STYLES["muted"]))

    _section(console, "providers (the LLM ids Roboglot carries: roboglot list-models)")
    console.print(_providers_table(report))
    if report.llm_env_error:
        console.print(Text(report.llm_env_error, style=ui.STYLES["warn"]), soft_wrap=True)

    _section(
        console,
        "discrete stepper: decision LLMs (roboglot run --decision-llm; off unless you name one)",
    )
    console.print(_steppers_table(report))
    console.print(
        ui.plain(
            "One of these answers the turns that are a choice among calls this body can make. "
            "Every pose, every sentence and every verdict is still the pilot LLM's "
            "(docs/guides/decision-llms/README.md).",
            style=ui.STYLES["muted"],
        )
    )

    at_host = report.host is not None and bool(report.host.address)
    # the 1.5 s is PROBE_TIMEOUT_S, written out because tests/test_llm_wording.py reads these
    # two titles in the source
    if report.host is not None and at_host:
        machine = host_of(report.host.host)
        _section(console, f"LLM servers, the presets on {machine} (GET /v1/models, 1.5 s timeout)")
    else:
        _section(console, "local LLM servers (GET /v1/models, 1.5 s timeout)")
    console.print(_servers_table(report))
    for server in report.servers:
        if server.placement is None:
            continue
        console.print(
            Text(f"  where {server.preset} put its LLMs (GET /api/ps)", style=ui.STYLES["muted"])
        )
        tegra = at_host and report.host is not None and report.host.is_tegra
        console.print(_placement_grid(server, tegra=tegra and server.preset != "local"))

    _section(console, "adapters (--robot <adapter>:<backend>)")
    console.print(ui.adapters_table(report.adapters, title=None))

    if report.robot is not None:
        _section(console, report.robot.spec)
        if report.robot.error:
            console.print(ui.fail_line(report.robot.error, hint=_ADAPTER_HINT))
        else:
            console.print(_verbs_table(report.robot))
            if report.robot.probe is not None:
                if report.robot.probe.error:
                    console.print(ui.fail_line(report.robot.probe.error))
                else:
                    console.print(_probe_table(report.robot.probe))
                for advisory in report.robot.probe.advisories:
                    console.print(Text(advisory, style=ui.STYLES["warn"]), soft_wrap=True)

    _section(console, "transports (backends an adapter checks on this machine)")
    console.print(_transports_table(report))
    console.print(ui.plain(TEAM_NOTE, style=ui.STYLES["muted"]))

    _section(console, "optional extras")
    console.print(_checks(report.extras, missing_is_fine=True))

    _section(console, f"upstream assumptions (UNVERIFIED: {len(report.assumptions)})")
    console.print(_assumptions_table(report))
    console.print(_pins_table(report))
    console.print(_read_more(report))

    console.print()
    console.print(verdict(report))


def run_doctor(
    console: Console,
    robot: str | None = None,
    *,
    address: str | None = None,
    camera_url: str | None = None,
    token: str | None = None,
    host: str | None = None,
    host_token: str | None = None,
    progress: Progress = None,
) -> bool:
    """Collect, render, and say whether this machine is in a state to run anything.

    With a robot and an address it connects the body, so it goes through the hooks `roboglot
    doctor` passes (`cli._DoctorHands`): the warning to support an arm before the connect, the
    countdown for the hands to come away before the fold, the kill switch that stops the fold,
    and, at a terminal, the question over an arm whose close keeps its torque. It called
    `collect` with none of them, so a library caller handing it an arm connected and folded the
    arm with no word and no way to stop it."""
    from roboglot.adapters.factory import needs_rest_pose
    from roboglot.cli import _can_prompt, _deprecated_backends, _doctor_warning, _DoctorHands
    from roboglot.cli import _warn_line as warn_line
    from roboglot.safety import AfterTheClose

    # as the CLI's doctor builds them, for a robot with no rest pose recorded: this function has
    # never taken one
    hands = _DoctorHands(
        robot or "",
        prompting=_can_prompt(),
        unposed=bool(robot) and needs_rest_pose(robot or ""),
    )

    def warn(adapter: Any) -> None:
        # what the CLI's doctor says just before the probe connects (`cli.doctor`'s `warn`)
        if robot:
            _deprecated_backends([robot])
        if getattr(adapter, "supports_hand_off", False):
            line = _doctor_warning(
                folds=getattr(adapter, "rest_pose", None) is not None,
                keeps=hands.unposed,
                asks=hands.prompting,
            )
            console.print(warn_line(line))
        hands.start(adapter)

    try:
        report = collect(
            robot,
            address=address,
            camera_url=camera_url,
            token=token,
            host=host,
            host_token=host_token,
            progress=progress,
            before_connect=warn,
            park=hands.park,
            before_close=hands.before_close,
            after_close=hands.finish,
        )
    finally:
        hands.finish()
    AfterTheClose.not_stuck_yet()
    render(console, report)
    return report.ok
