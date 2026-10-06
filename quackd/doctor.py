"""`quackd doctor`: what can run here, and what this machine is assuming about the robot.

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
import importlib
import importlib.metadata as md
import os
import platform
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from rich.console import Console
from rich.rule import Rule
from rich.text import Text

from quackd import __version__, ui
from quackd.adapters.base import AdapterError, RestResult, go_to_rest_if_any
from quackd.adapters.factory import (
    RobotSpec,
    adapter_names,
    describe,
    is_installed,
    is_simulator,
    list_adapters,
    parse_robot_spec,
)
from quackd.adapters.factory import (
    _module as _adapter_module,
)
from quackd.agent.providers.base import ProviderError
from quackd.agent.providers.factory import (
    DEFAULT_MODELS,
    EXTRA_FOR,
    KEY_ENV,
    LOCAL_NAMES,
    PROVIDER_NAMES,
    SDK_FOR,
    parse_llm,
    resolve_model,
)
from quackd.agent.providers.local import PRESETS, on_host
from quackd.command import redacted_url
from quackd.duckfile.parser import list_bundled_ducks
from quackd.host import (
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
# them all here would be a list to keep in step for no gain; `quackd list-models` and the
# providers table above already say which vendor wants which install.
EXTRAS = {
    "anthropic": ("anthropic", "quackd[anthropic]"),
    "openai": ("openai", "quackd[openai] and every OpenAI-compatible vendor"),
    "gemini": ("google.genai", "quackd[gemini]"),
    "decision (System One client)": ("typesafe_sdk", "quackd[decision]"),
    "laya (in-process decision LLM)": ("laya", "quackd[laya]"),
    "yolo": ("ultralytics", "quackd[yolo]"),
    "live": ("pygame", "quackd[live]"),
    "mujoco": ("mujoco", "quackd[mujoco]"),
    "lan (zeroconf)": ("zeroconf", "quackd[lan]"),
    "lan (mqtt)": ("paho.mqtt.client", "quackd[lan]"),
    "lerobot": ("lerobot", "quackd[lerobot]"),
    "lerobot (feetech bus)": ("scservo_sdk", "quackd[lerobot]"),
    "lerobot-sim (mujoco)": ("mujoco", "quackd[lerobot-sim]"),
    "lerobot-vla (transformers)": ("transformers", "quackd[lerobot-vla]"),
    "rosbridge": ("roslibpy", "quackd[rosbridge]"),
    "microduck camera (webrtc)": ("aiortc", "quackd[microduck-camera]"),
    "xlerobot": ("zmq", "quackd[xlerobot]"),
    "alohamini": ("zmq", "quackd[alohamini]"),
}
# Packages looked up by distribution metadata only, never imported: importing lerobot pulls
# torch into a diagnostics command, which is exactly what doctor is not. The Feetech SDK is the
# half of `quackd[lerobot]` that opens the serial port, and a lerobot installed without its
# `[feetech]` extra imports cleanly and then cannot reach an arm, so doctor asks for it by name.
# Laya is here for the same reason as lerobot rather than for its own: it is the one decision
# LLM that runs in this process, so it carries torch too, and the decision table below prints a
# version for every row on a machine that is only reading the table. transformers is the half
# of `quackd[lerobot-vla]` the arm's own extra lacks, the one a SmolVLA or a pi05 checkpoint
# loads its tokenizer with in the policy server, and importing it costs seconds.
_METADATA_ONLY = {
    "lerobot": "lerobot",
    "scservo_sdk": "feetech-servo-sdk",
    "laya": "laya",
    "transformers": "transformers",
}

CORE_MODULES = (
    ("pydantic", "pydantic"),
    ("mcp", "mcp"),
    ("opencv", "cv2"),
    ("numpy", "numpy"),
    ("Pillow", "PIL"),
)

Progress = Any
"""`progress(message)`, or None: what the spinner says while a slow question is asked."""

_ADAPTER_HINT = "quackd list-adapters shows the seven that ship and their backends"

FLOCK_NOTE = (
    "flock mode (--flock, flock.roles): sim2d only, in-process bus by default. The MQTT bus "
    "(quackd[lan]) is library-only (docs/guides/lan.md)."
)


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
    dist = {"google.genai": "google-genai", "paho.mqtt.client": "paho-mqtt"}.get(module, module)
    try:
        return md.version(dist)
    except md.PackageNotFoundError:
        pass
    # An import name is not a distribution name: `cv2` ships as opencv-python-headless here
    # and as opencv-python elsewhere, `PIL` as pillow. Ask the installer which one it was,
    # rather than printing "?" next to a package that is plainly installed and working.
    for candidate in md.packages_distributions().get(module.split(".")[0], []):
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
    model: str
    url: str = ""
    """Where a self-hosted decision LLM listens, and empty for everything that has no address:
    every provider, whose vendor owns its own, and the hosted decision LLM, whose SDK does."""
    pinned: bool = False
    """The environment named this model rather than the row taking its default: QUACKD_LLM for
    a provider, the SDK's own variable for a decision LLM that honours one."""
    refused_model: str = ""
    """A QUACKD_LLM whose model half this vendor does not list, which is why `model` is the
    vendor's default and not what the environment asked for."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "extra": self.extra,
            "version": self.version,
            "key": self.key or None,
            "key_env": self.key_env,
            "key_optional": self.key_optional,
            "model": self.model,
            "url": self.url or None,
            "pinned": self.pinned,
            "refused_model": self.refused_model or None,
        }


@dataclass
class PlacementRow:
    """One model Ollama has loaded, and how much of it sits in GPU memory.

    Ollama's own `/api/ps` reports both sizes, and the gap between them is the question a
    Jetson owner cannot see from the laptop: a model that answers, slowly, because it is on the
    CPU looks exactly like one that answers quickly until somebody times it."""

    model: str
    size: int
    """Bytes the loaded model takes, as Ollama counts them."""
    size_vram: int
    """Bytes of that in GPU memory. Equal to `size` is all on the GPU, zero is all on the CPU."""

    @property
    def gpu_pct(self) -> int:
        """The share on the GPU, rounded down, and never 0 for a model with any of itself there:
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
            "model": self.model,
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

    The only way to see the difference between quackd's description of a fully built robot and
    the one somebody assembled, before a run finds it. It is one of the parts of doctor that
    leave this machine, with the model servers it probes and the board `--host` names."""

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
    """What an NVIDIA Jetson says about itself, for whoever is putting a model on it.

    Read over the network: quackd never runs on the board, and the daemon it ships for it
    (`bridge/jetson/quackd_jetson_hostd.py`) sends the board's own files and command output as
    raw text for `_jetson_from_dump` to parse here. Informational, and never part of `ok`: the
    GPU belongs to the model server and the daemon's detector, and a board with no swap is a
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
        a model that does not fit: compressing memory cannot hold what memory could not."""
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
    """The machine `--host` names, as the daemon quackd ships for it described it.

    `ok` is one question: did the daemon answer `/hello`, with the token it was given, in the
    protocol this quackd speaks. That is what a run needs before it will start with `--host`,
    so it is what fails this report, the way a robot that does not answer `--address` fails it.
    Everything the daemon says after that is information: its health, a camera that stopped, a
    board with no swap. A `/healthz` or `/board` that fails once `/hello` has answered is kept
    as an error of its own and shown as a warning, because a run would still start.

    The token is never here: `HostClient` replaces it in every error it raises and in every
    reply it returns, so a daemon that echoes it in a reason, a hostname or a board file puts
    `<token>` on the screen and in --json, and nothing else in this report is built from it."""

    host: str
    """As it was given: `--host`, a registered robot's host, or `QUACKD_HOST`. Empty when the
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
    """True when `host` found no address, so no model server there is asked either: each would
    wait on the same failed lookup and then read as a machine whose servers are off. Not in
    --json, where `error` says it in words and every preset row says it was not asked."""
    jetson: JetsonReport | None = None
    """None when the board is not a Tegra, or its dump could not be had."""
    servers: list[ServerRow] = field(default_factory=list)
    """The four local presets as they were probed on this machine, Ollama's placement rows
    among them. The same rows the report's own `servers` lists, kept here as well so a reader
    of `--json` finds the board's model server under the board: whether a model sits on the
    Jetson's GPU is a fact about the board, and the top-level list also holds `local`, which
    is wherever QUACKD_BASE_URL points and may be no part of it."""

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
    """The duck-ipc-proto version quackd speaks. The one number here that is about the wire
    rather than about this machine, and the first thing to check against a robot."""
    core: list[Check] = field(default_factory=list)
    bundled_ducks: int = 0
    providers: list[ProviderRow] = field(default_factory=list)
    llm_env_error: str = ""
    """Why QUACKD_LLM names no row of the table above, when it names none.

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
    """None unless a board was named: `--host`, a registered robot's host, or `QUACKD_HOST`."""

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
            "bundled_ducks": self.bundled_ducks,
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


def _probe_models(base_url: str, timeout_s: float = 1.5) -> tuple[str, str]:
    """Reachability of an OpenAI-compatible server, as (state, detail). Never raises.

    Whatever answered is read with no trust in its shape. A reply that is JSON but not an
    object, or an object whose `data` is null or a number, used to raise out of `collect()` from
    outside the `try`, which took the whole report with it; a model server on another machine
    makes a stranger answer likelier, and doctor is the command people run when something is
    already wrong. `"data": null` is read as no models, the way `"data": []` is: a server with
    nothing to list may say either, and neither is a stranger. Any other `data` that is not a
    list gets the row that says so, where text or an object there used to read as a server with
    no models loaded."""
    import json
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(f"{base_url.rstrip('/')}/models", timeout=timeout_s) as r:
            payload = json.loads(r.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        return "http", f"HTTP {e.code}"
    except Exception:
        # A body that is not JSON lands here too, and reads as no model server there, which is
        # what a dev web server's index page on 8000 or 8080 is. That is the row this probe has
        # always given it; only the JSON shapes below have rows of their own.
        return "down", "not running"
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(payload, dict) or not isinstance(data, list | None):
        return "http", "answered /models with something that is not a list of models"
    ids = [str(m.get("id", "")) for m in data or [] if isinstance(m, dict)]
    shown = ", ".join(i for i in ids[:3] if i)
    more = f" (+{len(ids) - 3})" if len(ids) > 3 else ""
    return "up", f"{shown}{more}" if ids else "no models loaded"


def _ollama_root(url: str) -> str:
    """The Ollama preset's address without its OpenAI-compatible `/v1`: Ollama's own API,
    `/api/ps` among it, lives at the root."""
    root = url.rstrip("/")
    return root[: -len("/v1")] if root.endswith("/v1") else root


def _probe_placement(root_url: str, timeout_s: float = 1.5) -> tuple[list[PlacementRow], str]:
    """What an Ollama at `root_url` has loaded and where, from its own `GET /api/ps`, as
    (rows, note). The note says why there are no rows. Never raises, and nothing it finds
    touches `ok`: a model on the CPU is slow, not broken.

    A module attribute for the reason `_probe_models` is one: the tests replace it."""
    import json
    import urllib.error
    import urllib.request

    url = f"{root_url.rstrip('/')}/api/ps"
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        return [], f"GET /api/ps answered HTTP {e.code}, so this may not be Ollama"
    except Exception:
        return [], "GET /api/ps did not answer"
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except Exception:
        return [], "GET /api/ps answered with something that is not JSON"
    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(payload, dict) or not isinstance(models, list | None):
        return [], "GET /api/ps answered with something that is not Ollama's list"

    def size_of(value: Any) -> int | None:
        # bool is an int to Python and not a size to anybody
        ok = isinstance(value, int) and not isinstance(value, bool) and value >= 0
        return int(value) if ok else None

    rows: list[PlacementRow] = []
    unplaced: list[str] = []
    for item in models or []:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("model")
        if not isinstance(name, str) or not name:
            continue
        size, vram = size_of(item.get("size")), size_of(item.get("size_vram"))
        # a model of no size has no split to report, and would read as "on the CPU"
        if size and vram is not None:
            rows.append(PlacementRow(model=name, size=size, size_vram=vram))
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
    # Ollama loads a model on its first request and unloads it after five idle minutes, so an
    # empty list on a healthy server is the usual answer and not a fault
    return [], "nothing loaded yet: Ollama loads a model on its first request"


def probe(
    spec: str,
    static: Any,
    address: str,
    camera_url: str | Sequence[str] | None,
    token: str | None,
    rest_pose: dict[str, float] | None = None,
    *,
    name: str | None = None,
    before_connect: Callable[[Any], None] | None = None,
    host: HostClient | None = None,
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
    one chance to say something to a person holding it (`cli._doctor_warning`). `address` is
    empty only for a registered simulator, which is built without one as a run builds it.

    `host` is the board `--host` names, when its daemon answered. Its camera joins the body
    as a run's would (`quackd.adapters.host_camera`), and the camera rows say whether it is
    the primary view or an extra one. The body's own cameras are read only with
    `--camera-url`, as before, because only then was anybody asking about them."""
    import asyncio

    from quackd.adapters.factory import make_adapter
    from quackd.adapters.host_camera import HOST_CAMERA_NAME, HostCameraAdapter
    from quackd.transport.base import DEFAULT_CAMERA_NAME, TransportError, frames_of

    async def go() -> tuple[
        Any, Any, dict[str, Any] | None, dict[str, Any], RestResult, str | None, list[str]
    ]:
        parsed = parse_robot_spec(spec)
        adapter = make_adapter(
            RobotSpec(parsed.adapter, parsed.backend, name) if name else parsed,
            address=address or None,
            camera_url=camera_url,
            token=token,
            rest_pose=rest_pose,
            host=host,
        )
        if before_connect is not None:
            before_connect(adapter)
        live = await adapter.connect()
        hosted = isinstance(adapter, HostCameraAdapter)
        # the connect attempts a body had to make again, read here and not after the close:
        # the list belongs to the connect that just happened
        retried = [str(n) for n in getattr(adapter, "connect_notes", ()) or ()]
        transport = getattr(adapter, "transport", None)
        # What the robot says about its own guarantees, rather than what quackd's static
        # description claims on its behalf. This is the checklist's go/no-go gate, so a
        # deadman window or an absent token has to be visible here.
        told: dict[str, Any] = dict(getattr(transport, "safety", None) or {})
        for key in ("auth_warning", "runtime_warning"):
            if warning := getattr(transport, key, None):
                told[key] = warning
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
            parked = await go_to_rest_if_any(adapter)
            # close_note is written by the disconnect, so the disconnect happens here and is
            # read from; the finally below is left as the safety net for the exception path.
            await adapter.disconnect()
            closed = True
            note = getattr(transport, "close_note", None)
            return live, health, camera, told, parked, note, retried
        finally:
            if not closed:
                await adapter.disconnect()

    try:
        live, health, camera, told, parked, note, retried = asyncio.run(go())
    except (TransportError, OSError, HostError) as e:
        where = f" at {address}" if address else ""
        return ProbeReport(address=address, ok=False, error=f"{spec}{where}: {e}")

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
        add(ProbeRow("safety", "as this bridge reported it"))
        for key in (
            "deadman_ms",
            "auth",
            "fall_detection",
            "getup_policy",
            "estop",
            "runtime_commit",
        ):
            if key in told:
                reported = told[key]
                worrying = (key == "auth" and reported == "none") or (
                    key in ("fall_detection", "getup_policy") and reported is False
                )
                add(ProbeRow(f"  {key}", str(reported), "warn" if worrying else "plain"))
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
            add(ProbeRow("  url", str(cam.get("url") or camera_url)))
            if cam.get("error"):
                add(ProbeRow("  error", str(cam["error"]), "fail"))
    rest_ok = parked.reached or not parked.recorded
    if not parked.recorded:
        add(ProbeRow("rest pose", "none recorded (quackd robot rest-pose <name>)"))
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
            f"a .duck that requires {lost[0]} will be refused on this robot, and one that "
            "merely allows it runs without it"
        )
    if not camera_ok:
        # naming verbs this body does not have sends the reader looking for them at the
        # moment they are trying to work out why their webcam gave nothing
        from quackd.verbs.core import REQUIREMENTS

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
    for key in ("auth_warning", "runtime_warning"):
        if warning := told.get(key):
            report.advisories.append(str(warning))
    if told.get("fall_detection") is False:
        report.advisories.append(
            "nothing on this robot detects a fall, so posture never becomes 'fallen' and no "
            "verb refuses because it is down. You are the fall detector: keep it on a stand "
            "and watch it."
        )
    # `note` and not just `rest_ok`: the rest move can report that it arrived and the
    # disconnect's own re-read still find the arm away, or fail to read it at all. That is the
    # case where the arm is left energised after a command somebody ran to be reassured, so it
    # cannot also be the case where the panel is green and the exit code is 0.
    report.ok = bool(health.ok) and camera_ok and rest_ok and not note
    return report


def _microduck_api_version() -> str:
    """The `duck-ipc-proto` version quackd speaks, when the duck is installed to say so.

    Empty when it is not, which is what a machine with no Microduck on it should read: the
    number belongs to that robot's protocol and means nothing without it."""
    with contextlib.suppress(Exception):
        from quackd_microduck import upstream_api as robotd

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
    from quackd.adapters.factory import _module, adapter_names, is_installed

    rows: list[tuple[str, Any, str, str]] = []
    for name in adapter_names():
        if not is_installed(name):
            continue
        with contextlib.suppress(Exception):
            rows.extend(tuple(row) for row in getattr(_module(name), "UPSTREAMS", ()))
    return rows


# ── the board --host names, over the network ────────────────────────────────────────────

_host_client = HostClient
"""How doctor reaches the board, and a module attribute for the reason `_probe_models` is one:
a test replaces it. It must never become a default argument, which would bind at import and
ignore the monkeypatch."""

_L4T_LINE = re.compile(r"R(\d+)\s*\(release\),\s*REVISION:\s*(\d+(?:\.\d+)*)")
"""`# R36 (release), REVISION: 4.3, GCID: ...` is the first line of `/etc/nv_tegra_release`."""

_GPU_BUSY = re.compile(r"GR3D_FREQ\s+(\d+)%")
"""The GPU's load in a `tegrastats` line. Orin prints `GR3D_FREQ 0%@[305]`, the clock after the
`@`, and older boards `GR3D_FREQ 0%@1300`; the percentage comes first in both."""

_JETPACK_FOR_L4T = {
    "36.5.2": "6.2.3",
    "36.5.0": "6.2.2",
    "36.4.4": "6.2.1",
    "36.4.3": "6.2",
    "36.4.0": "6.1",
    "36.3.0": "6.0",
    "39.2.1": "7.2.1",
    "39.2.0": "7.2",
    "38.4.0": "7.1",
    "38.2.1": "7.0",
    "38.2.0": "7.0",
}
"""Exact releases only, read off NVIDIA's JetPack archive on 2026-09-22.

`docs/guides/jetson.md` prints this same table and `tests/test_docs.py` holds the two to each other,
so a board whose JetPack shipped after this was written cannot read as one version in the
documentation and another on the screen."""

_JETPACK_MAJOR = {"32": "4.x", "35": "5.x", "36": "6.x", "38": "7.x", "39": "7.x"}
"""What to say about a revision the table above has not heard of. The major version only, on
purpose: L4T 35.1 was JetPack 5.0.2 and 35.2.1 was 5.1, so a guessed minor here would be wrong
about a board somebody owns."""


def _parse_l4t(text: str) -> str | None:
    match = _L4T_LINE.search(text)
    return f"{match.group(1)}.{match.group(2)}" if match else None


def _jetpack_for(l4t: str) -> str | None:
    parts = [p for p in l4t.split(".") if p]
    if not parts:
        return None
    # padded to three, because NVIDIA writes JetPack 6.1's as "36.4" and the archive's own
    # table spells the same release "36.4.0"
    exact = ".".join([*parts, "0", "0"][:3])
    return _JETPACK_FOR_L4T.get(exact) or _JETPACK_MAJOR.get(parts[0])


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


def collect(
    robot: str | None = None,
    *,
    address: str | None = None,
    camera_url: str | Sequence[str] | None = None,
    token: str | None = None,
    rest_pose: dict[str, float] | None = None,
    host: str | None = None,
    host_token: str | None = None,
    progress: Progress = None,
    robot_name: str | None = None,
    before_connect: Callable[[Any], None] | None = None,
    host_token_fix: str | None = None,
) -> DoctorReport:
    """Every question doctor asks, answered as data.

    Nothing in here decides what anything looks like, which is what lets `--json` exist and
    what keeps `render` honest about where its numbers came from.

    `robot_name` is the registered name `robot` was resolved from, when it was, and the robot
    is described and built under it, as a run builds it; `before_connect` goes to `probe`.
    Not `name`, which this function's own loops over providers and presets rebind.

    `host` is a board already settled by `quackd.host.resolve_host` (the flag, a registered
    robot's, or `QUACKD_HOST`), and `host_token` the token that goes with it. With one, the
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

    say("checking the core packages")
    for name, module in CORE_MODULES:
        version = _installed(module)
        report.core.append(Check(name, version is not None, version or "missing"))
    report.bundled_ducks = len(list_bundled_ducks())

    say("checking the providers")
    # QUACKD_LLM names one vendor and, after the colon, at most one model of that vendor's, so
    # it pins exactly one row of this table and says nothing about the other sixteen. Read once
    # here rather than once per row: the variable it replaced was a bare model id with no
    # vendor on it, so every row had to guess whether it was the one being talked about.
    env_llm = os.environ.get("QUACKD_LLM", "").strip()
    env_vendor, env_model = "", None
    if env_llm:
        try:
            env_vendor, env_model = parse_llm(env_llm, source="QUACKD_LLM")
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
        # What this provider would actually be given, not what the table used to guess. The
        # vendor QUACKD_LLM names can still be handed a model it does not list, and doctor is
        # where a reader should find that out rather than three commands later.
        wanted = env_model if name == env_vendor else None
        refused = ""
        try:
            model = resolve_model(name, wanted, source="QUACKD_LLM") or "auto (first served)"
        except ProviderError:
            model = DEFAULT_MODELS.get(name) or "auto (first served)"
            refused = env_llm
        report.providers.append(
            ProviderRow(
                name=name,
                extra=f"quackd[{EXTRA_FOR[name]}]",
                version=_installed(SDK_FOR[name]),
                key=_mask(key) if key else "",
                key_env=KEY_ENV[name],
                key_optional=name in LOCAL_NAMES,
                model=model,
                pinned=name == env_vendor and not refused,
                refused_model=refused,
            )
        )

    # Asked for whether or not anybody uses one, because "which decision LLM could run here?"
    # is a question a reader of this screen should be able to answer without starting a task,
    # and the answer is almost always "none of them yet". Every row quackd names is printed on
    # a machine with none of them installed: this is a catalogue and not an inventory, so the
    # missing rows are the informative ones. Nothing here is probed over the network -- the
    # providers table does not knock on OpenAI either, and these servers do not all publish a
    # listing endpoint to knock on.
    from quackd.agent.decision.catalogue import IN_PROCESS, SYSTEM_ONE
    from quackd.agent.decision.factory import (
        find_preset,
        preset_names,
        resolve_decision_model,
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
            # QUACKD_DECISION_URL is a URL a person typed, so it can carry a password in its
            # userinfo the way --base-url can, and this screen is pasted into issues.
            where = redacted_url(where)
        elif spec.backend == SYSTEM_ONE and spec.key_env is None:
            # `local` is the row that exists to be told an address, and `make_decision_llm`
            # refuses it without one. The same sentence here as in that refusal, so the fix a
            # reader copies off this screen is the one the run would have asked them for.
            where = "unset: --decision-url or QUACKD_DECISION_URL"
        report.steppers.append(
            ProviderRow(
                name=spec.name,
                extra=f"quackd[{spec.extra}]" if spec.extra else "",
                # A plugin was found through its own metadata, so it is installed by
                # definition and has no import to probe: "?" is this file's word for "here,
                # and it did not say which version".
                version=_installed(spec.sdk) if spec.sdk else "?",
                key=_mask(decision_key) if decision_key else "",
                key_env=spec.key_env or "",
                # A server you run yourself wants no key and is sent NO_KEY, so an empty cell
                # there is not a machine with something missing.
                key_optional=spec.key_env is None,
                model=resolve_decision_model(spec) or "auto (the server names it)",
                url=where,
                pinned=bool(spec.model_env and os.environ.get(spec.model_env)),
            )
        )

    custom = os.environ.get("QUACKD_BASE_URL")
    # With a board, the four presets are asked on it, as a run with --host would reach them;
    # `local` has no preset address and keeps meaning QUACKD_BASE_URL. A host that is no
    # machine was already reported above, and its presets stay where they were. A daemon that
    # is down or refused still has its board's presets asked, since the model server can be up
    # without it; a name that found no address has none asked, since there is no machine to ask.
    board = report.host if report.host is not None and report.host.address else None
    for preset, url in {**PRESETS, **({"local": custom} if custom else {})}.items():
        if not url:
            report.servers.append(
                ServerRow(preset, "", "unset", "set QUACKD_BASE_URL or --base-url")
            )
            continue
        on_board = False
        if board is not None and preset != "local":
            with contextlib.suppress(ValueError):
                url, on_board = on_host(url, board.host), True
        # QUACKD_BASE_URL is a URL a person typed, so it can carry a password in its userinfo,
        # and this screen and its spinner are what people paste into issues
        shown = redacted_url(url)
        if board is not None and on_board and board.unresolved:
            skipped = ServerRow(
                preset, shown, "skipped", f"not asked: {host_of(board.host)} has no address"
            )
            report.servers.append(skipped)
            board.servers.append(skipped)
            continue
        say(f"probing {preset} at {shown}")
        state, detail = _probe_models(url)
        row = ServerRow(preset, shown, state, detail)
        if preset == "ollama" and state == "up":
            root = _ollama_root(url)
            say(f"asking ollama at {redacted_url(root)} where its models are")
            row.placement, row.placement_note = _probe_placement(root)
        report.servers.append(row)
        if board is not None and on_board:
            board.servers.append(row)

    report.adapters = list_adapters()

    if robot is not None:
        say(f"describing {robot}")
        try:
            parsed = parse_robot_spec(robot)
            manifest = describe(
                RobotSpec(parsed.adapter, parsed.backend, robot_name) if robot_name else parsed
            )
        except AdapterError as e:
            report.robot = RobotReport(spec=robot, error=str(e))
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
            # A registered simulator is connected with or without an address. Its connect reads
            # the calibration a run under that name would read, and renders once, which is the
            # only check that this machine can draw its scene, and nothing real moves. Skipping
            # it reported a machine healthy on which the run would be refused at connect. A
            # real body with no address is still never guessed at.
            simulated = robot_name is not None and is_simulator(parsed)
            if address or simulated:
                shown = f"{robot_name} ({robot})" if robot_name else robot
                say(f"connecting to {shown}" + (f" at {address}" if address else ""))
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
                    before_connect=before_connect,
                    host=host_camera_from,
                )

    # An adapter that has backends worth probing on this machine says so itself. The LeRobot
    # arm's simulator is the one today: whether MuJoCo is installed, and whether the SO-101's
    # model is in the cache at its pin. That knowledge belongs to the adapter rather than to a
    # table here that would have to be edited whenever somebody publishes an adapter, and each
    # row names its own `adapter:backend`, since the table is no longer one robot's.
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
    """The board the daemon runs on, for somebody about to put a model on it.

    Warnings only where a person would have to do something about it: swap that cannot hold a
    model. The GPU node is a note rather than a warning, because quackd reading the board
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
                "none. A model that does not fit in memory cannot load, and a swapfile on the "
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
            "none the daemon could see. The model server and the detector want one, and "
            "quackd never asks for one",
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
    """Where Ollama put each model it has loaded. A model on a Jetson's CPU is the pitfall the
    Jetson page names, so that one says what to do about it; on any other machine a model on
    the CPU may be all there is, and it is a note."""

    def build(g: ui.Glyphs) -> Any:
        rows: list[tuple[str, Any]] = []

        def row(key: str, value: str, style: str = "muted", mark: str = "") -> None:
            rows.append((f"{mark or g.note} {key}", Text(value, style=ui.STYLES[style])))

        for model in server.placement or []:
            name = _flat(model.model, 80)
            if model.where == "gpu":
                row(name, f"all on the GPU, {_gib(model.size)}", "ok", g.ok)
            elif model.where == "partial":
                row(name, f"{model.gpu_pct}% on the GPU, the rest on the CPU", "warn", g.warn)
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
            # flattened, because the note can name the models another machine's Ollama sent
            row("loaded", _flat(server.placement_note or "nothing"))
        return ui.kv_grid(rows, key_style="")

    return ui.Deferred(build)


def _providers_table(report: DoctorReport) -> Any:
    table = ui.table()
    table.add_column("provider", style=ui.STYLES["key"], no_wrap=True)
    table.add_column("extra")
    table.add_column("key")
    # folded, not elided: a model id with an ellipsis through it cannot be pasted into --llm
    table.add_column("default model", overflow="fold")
    for row in report.providers:
        if row.name == "fake":
            table.add_row(
                Text("fake"), Text("built-in", style=ui.STYLES["ok"]), Text("-"), Text(row.model)
            )
            continue
        extra = (
            Text(str(row.version), style=ui.STYLES["ok"])
            if row.version
            else Text.assemble(("missing ", ui.STYLES["warn"]), (f"({row.extra})", ""))
        )
        key: Any
        if row.key:
            key = ui.plain(row.key, style=ui.STYLES["ok"])
        elif row.key_optional:
            key = Text("optional", style=ui.STYLES["muted"])
        else:
            key = Text(f"{row.key_env} unset", style=ui.STYLES["warn"])
        if row.refused_model:
            model = Text.assemble(
                (f"QUACKD_LLM={row.refused_model}", ui.STYLES["warn"]),
                (f", which {row.name} does not list", ui.STYLES["warn"]),
            )
        else:
            model = Text(row.model, style=ui.STYLES["ok"] if row.pinned else "")
        table.add_row(Text(row.name), extra, key, model)
    return table


def _steppers_table(report: DoctorReport) -> Any:
    """The providers table's shape, so a reader recognises it, and its own section, so nobody
    reads a decision LLM as something `--llm` takes.

    One row per decision LLM quackd names, plus whatever a plugin added, installed or not: the
    table is here to show what this machine would have to install, so a row for something that
    is not here yet is the one worth printing. The url column is the difference from the
    providers table, because most of these are a server somebody runs themselves."""
    table = ui.table()
    table.add_column("decision llm", style=ui.STYLES["key"], no_wrap=True)
    table.add_column("extra")
    table.add_column("key")
    table.add_column("model", overflow="fold")
    # folded, not elided: a url with an ellipsis through it cannot be pasted into --decision-url
    table.add_column("url", overflow="fold")
    for row in report.steppers:
        extra = (
            Text(str(row.version), style=ui.STYLES["ok"])
            if row.version
            else Text.assemble(("missing ", ui.STYLES["warn"]), (f"({row.extra})", ""))
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
            Text(row.model, style=ui.STYLES["ok"] if row.pinned else ""),
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
            # the detail is the model ids, which are the answer; the word is the good news
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
    # a registered simulator is probed with no address, and its table has no place to name
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
    table.add_column("what")
    table.add_column("note", ratio=2)
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
        # "the simulator" only where there is one. Since every adapter became its own package
        # a green machine can have no robot at all, and this line is what `README.md` sends a
        # new reader to read: saying the simulator runs on a machine where `quackd run`
        # refuses for want of one is the opposite of what this command is for.
        installed = [row["name"] for row in report.adapters if row.get("adapter_installed")]
        if not installed:
            reason = (
                "quackd itself runs here, and no robot adapter is installed: "
                'uv pip install "quackd[microduck]" for the simulator'
            )
        elif "microduck" in installed:
            reason = "the simulator and the scripted pilot run here"
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
    counters = [
        f"{sum(1 for e in report.extras if e.ok)}/{len(report.extras)} extras",
        # a fraction, not a total: since every adapter became its own package the number that
        # matters is how many are here, and "7 adapters" beside a table of seven `not
        # installed` rows reads as seven you have
        f"{sum(1 for a in report.adapters if a.get('adapter_installed'))}/"
        f"{len(report.adapters)} adapters installed",
        f"{report.bundled_ducks} bundled ducks",
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
            (f"quackd {report.version}", ui.STYLES["key"]),
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
    console.print(Text(f"  {report.bundled_ducks} bundled ducks", style=ui.STYLES["muted"]))

    _section(console, "providers (every model id: quackd list-models)")
    console.print(_providers_table(report))
    if report.llm_env_error:
        console.print(Text(report.llm_env_error, style=ui.STYLES["warn"]), soft_wrap=True)

    _section(
        console,
        "discrete stepper: decision LLMs (quackd run --decision-llm; off unless you name one)",
    )
    console.print(_steppers_table(report))
    console.print(
        ui.plain(
            "One of these answers the turns that are a choice among calls this body can make. "
            "Every pose, every sentence and every verdict is still the model's "
            "(docs/guides/decision-llms/README.md).",
            style=ui.STYLES["muted"],
        )
    )

    at_host = report.host is not None and bool(report.host.address)
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
            Text(f"  where {server.preset} put its models (GET /api/ps)", style=ui.STYLES["muted"])
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
    console.print(ui.plain(FLOCK_NOTE, style=ui.STYLES["muted"]))

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
    """Collect, render, and say whether this machine is in a state to run anything."""
    report = collect(
        robot,
        address=address,
        camera_url=camera_url,
        token=token,
        host=host,
        host_token=host_token,
        progress=progress,
    )
    render(console, report)
    return report.ok
