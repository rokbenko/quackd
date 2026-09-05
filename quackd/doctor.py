"""`quackd doctor`: what can run here, and what this machine is assuming about the robot.

It exists because "it doesn't work" almost always means a missing extra, a missing key,
or an upstream assumption — and all three should be visible in one screen, before anyone
opens an issue.
"""

from __future__ import annotations

import importlib
import importlib.metadata as md
import os
import platform
import sys
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from quackd import __version__
from quackd.adapters.base import AdapterError
from quackd.adapters.factory import describe, list_adapters, parse_robot_spec
from quackd.agent.providers.factory import DEFAULT_MODELS, KEY_ENV, LOCAL_NAMES, PROVIDER_NAMES
from quackd.agent.providers.local import PRESETS
from quackd.duckfile.parser import list_bundled_ducks
from quackd.transport import upstream_api as up
from quackd.transport.factory import TRANSPORT_STATUS

EXTRAS = {
    "anthropic": ("anthropic", "quackd[anthropic]"),
    "openai": ("openai", "quackd[openai] / quackd[grok]"),
    "gemini": ("google.genai", "quackd[gemini]"),
    "yolo": ("ultralytics", "quackd[yolo]"),
    "live": ("pygame", "quackd[live]"),
    "reachy": ("reachy_mini", "quackd[reachy]"),
    "lan (zeroconf)": ("zeroconf", "quackd[lan]"),
    "lan (mqtt)": ("paho.mqtt.client", "quackd[lan]"),
    "lerobot": ("lerobot", "quackd[lerobot]"),
    "rosbridge": ("roslibpy", "quackd[rosbridge]"),
    "microduck camera (webrtc)": ("aiortc", "quackd[microduck-camera]"),
}
# Robot SDKs are looked up by distribution metadata only: importing reachy_mini pulls
# onnxruntime and GStreamer, and lerobot pulls torch, into a diagnostics command, which is
# exactly what doctor is not.
_METADATA_ONLY = {"reachy_mini": "reachy-mini", "lerobot": "lerobot"}


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


def _probe_models(base_url: str, timeout_s: float = 1.5) -> str:
    """Reachability of an OpenAI-compatible server, plus the first few model ids."""
    import json
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(f"{base_url.rstrip('/')}/models", timeout=timeout_s) as r:
            payload = json.loads(r.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        return f"[yellow]HTTP {e.code}[/yellow]"
    except Exception:
        return "[dim]not running[/dim]"
    ids = [str(m.get("id", "")) for m in payload.get("data", []) if isinstance(m, dict)]
    shown = ", ".join(i for i in ids[:3] if i)
    more = f" (+{len(ids) - 3})" if len(ids) > 3 else ""
    return f"[green]up[/green] · {shown}{more}" if ids else "[green]up[/green] · no models loaded"


def _probe(
    console: Console,
    robot: str,
    static: Any,
    address: str,
    camera_url: str | None,
    token: str | None,
) -> bool:
    """Connect, and report what the robot itself said.

    Everything else in this file is offline and reads the *static* manifest, which describes
    a fully built robot. A real one is whatever its owner assembled, so this is the only way
    to see the difference before a run does."""
    import asyncio

    from quackd.adapters.factory import make_adapter, parse_robot_spec
    from quackd.transport.base import TransportError

    async def go() -> tuple[Any, Any, dict[str, Any] | None, dict[str, Any]]:
        adapter = make_adapter(
            parse_robot_spec(robot), address=address, camera_url=camera_url, token=token
        )
        live = await adapter.connect()
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
        try:
            health = await adapter.health()
            # A camera URL that nothing checks is a camera URL that fails mid-run. doctor used
            # to accept --camera-url, hand it to the transport and never ask for a frame, so a
            # typo'd or unreachable snapshot server passed here and failed at the first observe.
            camera: dict[str, Any] | None = None
            probe = getattr(transport, "camera_health", None)
            # Only report on a camera the adapter actually reads. `camera_url` is accepted and
            # ignored by reachy_mini, lerobot and rosbridge, and gating their verdict on a
            # frame from an unrelated path fails a healthy robot.
            if camera_url and callable(probe):
                # Frames arrive on a timer, so ask for one and give the capture loop a moment
                # rather than reading memory that cannot have been filled yet.
                frame = await adapter.get_frame()
                for _ in range(50):
                    if frame is not None:
                        break
                    await asyncio.sleep(0.1)
                    frame = await adapter.get_frame()
                camera = dict(probe())
                camera["frame"] = f"{frame.width}x{frame.height}" if frame is not None else None
            return live, health, camera, told
        finally:
            await adapter.disconnect()

    try:
        live, health, camera, told = asyncio.run(go())
    except (TransportError, OSError) as e:
        console.print(f"[red]{robot} at {address}: {escape(str(e))}[/red]")
        return False

    t = Table(title=f"{robot} at {address} (what the robot itself reported)")
    t.add_column("what")
    t.add_column("value")
    t.add_row("connected", "[green]yes[/green]")
    t.add_row("health", "[green]ok[/green]" if health.ok else f"[red]{health.reason}[/red]")
    for key, value in (health.extras or {}).items():
        t.add_row(f"  {key}", "" if value is None else str(value))
    gained = sorted(set(live.verb_names()) - set(static.verb_names()))
    lost = sorted(set(static.verb_names()) - set(live.verb_names()))
    t.add_row("verbs", f"{len(live.verb_names())} of {len(static.verb_names())} described")
    if lost:
        t.add_row("  not on this robot", f"[yellow]{', '.join(lost)}[/yellow]")
    if gained:
        t.add_row("  beyond the description", f"[green]{', '.join(gained)}[/green]")
    for key, value in (live.extras.get("expression_features") or {}).items():
        t.add_row(f"  {key}", "[green]yes[/green]" if value else "[dim]no[/dim]")
    if told:
        # What the robot says about its own guarantees, not what quackd's static description
        # claims on its behalf. The deadman window is a free parameter and whether there is a
        # token at all is the difference between the documented setup and an open port, so
        # both belong in front of the operator at the checklist's go/no-go gate.
        t.add_row("safety", "[dim]as this bridge reported it[/dim]")
        for key in ("deadman_ms", "auth", "fall_detection", "getup_policy", "estop",
                    "runtime_commit"):
            if key in told:
                reported = told[key]
                worrying = (key == "auth" and reported == "none") or (
                    key in ("fall_detection", "getup_policy") and reported is False
                )
                shown = str(reported)
                t.add_row(f"  {key}", f"[yellow]{shown}[/yellow]" if worrying else shown)
    camera_ok = True
    if camera is not None:
        frame = camera.get("frame")
        camera_ok = frame is not None
        t.add_row(
            "camera",
            f"[green]{frame}[/green]" if camera_ok else "[red]no frame[/red]",
        )
        t.add_row("  url", str(camera.get("url") or camera_url))
        if camera.get("error"):
            t.add_row("  error", f"[red]{escape(str(camera['error']))}[/red]")
    console.print(t)
    if lost:
        console.print(
            f"[dim]a .duck that requires {lost[0]} will be refused on this robot, and one "
            "that merely allows it runs without it[/dim]"
        )
    if not camera_ok:
        console.print(
            "[yellow]--camera-url was given but no frame came back, so observe, go_to, "
            "search_scan and approach_and cannot see anything on this run[/yellow]"
        )
    for key in ("auth_warning", "runtime_warning"):
        if warning := told.get(key):
            console.print(f"[yellow]{escape(str(warning))}[/yellow]")
    if told.get("fall_detection") is False:
        console.print(
            "[yellow]nothing on this robot detects a fall, so posture never becomes "
            "'fallen' and no verb refuses because it is down. You are the fall "
            "detector: keep it on a stand and watch it.[/yellow]"
        )
    return bool(health.ok) and camera_ok


def run_doctor(
    console: Console,
    robot: str | None = None,
    *,
    address: str | None = None,
    camera_url: str | None = None,
    token: str | None = None,
) -> bool:
    ok = True
    console.print(
        f"[bold]quackd {__version__}[/bold] · Python {platform.python_version()} · "
        f"{platform.system()} {platform.release()}"
    )

    t = Table(title="core", show_header=False)
    for name, module in (
        ("pydantic", "pydantic"),
        ("mcp", "mcp"),
        ("opencv", "cv2"),
        ("numpy", "numpy"),
        ("Pillow", "PIL"),
    ):
        ver = _installed(module)
        t.add_row(name, f"[green]{ver}[/green]" if ver else "[red]missing[/red]")
        ok &= ver is not None
    t.add_row("bundled ducks", str(len(list_bundled_ducks())))
    console.print(t)

    t = Table(title="providers")
    t.add_column("provider")
    t.add_column("extra")
    t.add_column("key")
    t.add_column("default model")
    for name in PROVIDER_NAMES:
        if name == "fake":
            t.add_row("fake", "[green]built-in[/green]", "—", "scripted")
            continue
        module, extra = EXTRAS["openai" if name in ("grok", *LOCAL_NAMES) else name]
        ver = _installed(module)
        key = os.environ.get(KEY_ENV[name], "")
        model = os.environ.get("QUACKD_MODEL") or DEFAULT_MODELS.get(name) or "auto (first served)"
        if name in LOCAL_NAMES:
            key_cell = f"[green]{_mask(key)}[/green]" if key else "[dim]optional[/dim]"
        else:
            key_cell = (
                f"[green]{_mask(key)}[/green]" if key else f"[yellow]{KEY_ENV[name]} unset[/yellow]"
            )
        t.add_row(
            name,
            f"[green]{ver}[/green]" if ver else f"[yellow]missing[/yellow] ({escape(extra)})",
            key_cell,
            model,
        )
    console.print(t)

    t = Table(title="local LLM servers (GET /v1/models, 1.5 s timeout)")
    t.add_column("preset")
    t.add_column("base url")
    t.add_column("status")
    custom = os.environ.get("QUACKD_BASE_URL")
    for preset, url in {**PRESETS, **({"local": custom} if custom else {})}.items():
        if not url:
            t.add_row(preset, "[dim]set QUACKD_BASE_URL or --base-url[/dim]", "")
            continue
        t.add_row(preset, url, _probe_models(url))
    console.print(t)

    t = Table(title="adapters (--robot <adapter>:<backend>)")
    t.add_column("adapter")
    t.add_column("backends")
    t.add_column("status")
    t.add_column("extra")
    for row in list_adapters():
        # escape: an extra reads quackd[reachy], which Rich would eat as markup
        extra = escape(row["extra"])
        if row["extra"] != "built-in":
            extra += (
                " [green]installed[/green]" if row["installed"] else " [dim]not installed[/dim]"
            )
        t.add_row(row["name"], " · ".join(row["backends"]), row["status"], extra)
    console.print(t)
    if robot is not None:
        try:
            manifest = describe(parse_robot_spec(robot))
        except AdapterError as e:
            console.print(f"[red]{e}[/red]")
            ok = False
        else:
            t = Table(title=f"{robot}: {manifest.summary()}")
            t.add_column("verb")
            t.add_column("core")
            t.add_column("safety")
            t.add_column("preconditions")
            for spec in manifest.verbs:
                t.add_row(
                    spec.name,
                    "core" if spec.core else "",
                    spec.safety_class,
                    ", ".join(manifest.preconditions.get(spec.name, [])),
                )
            console.print(t)
            if address:
                ok &= _probe(console, robot, manifest, address, camera_url, token)

    t = Table(title="transports (Microduck backends; --robot microduck:<name>)")
    t.add_column("name")
    t.add_column("status")
    t.add_column("notes")
    for name, status in TRANSPORT_STATUS.items():
        note = ""
        if name == "jsonrpc":
            root = os.environ.get(up.RUNTIME_DIR_ENV.name, "/run")
            sock = Path(root) / "robotd.sock"
            if sys.platform == "win32":
                note = (
                    "Windows: use --address tcp://host:port via "
                    "`ssh -L 9870:/run/robotd.sock robot`"
                )
            elif sock.exists():
                note = f"[green]{sock} present[/green]"
            else:
                note = f"{sock} not found (not on a robot?)"
        if name == "websocket":
            note = up.WEBSOCKET_GATEWAY.note
        t.add_row(name, status, note)
    console.print(t)
    console.print(
        "[dim]flock mode (--flock, flock.roles): sim2d only, in-process bus by default. "
        "The MQTT bus (" + escape("quackd[lan]") + ") is library-only (docs/lan.md).[/dim]"
    )

    t = Table(title="optional extras", show_header=False)
    for label, (module, extra) in EXTRAS.items():
        ver = _installed(module)
        t.add_row(
            label,
            f"[green]{ver}[/green]" if ver else f"[dim]not installed ({escape(extra)})[/dim]",
        )
    console.print(t)

    unverified = up.refs_by_status("UNVERIFIED")
    t = Table(
        title=f"upstream assumptions (UNVERIFIED: {len(unverified)}) — see docs/transport-status.md"
    )
    t.add_column("what")
    t.add_column("note")
    for ref in unverified:
        t.add_row(ref.name, ref.note)
    console.print(t)
    console.print(
        f"[dim]upstream contract: duck-ipc-proto API v{up.API_VERSION.name} · "
        f"microduck upstream pinned at {up.PIN[:7]} (read {up.READ_ON}) · "
        f"VERIFIED refs: {len(up.refs_by_status('VERIFIED'))} · "
        "the jsonrpc backend has never been run against a robotd[/dim]"
    )

    from quackd.adapters.lerobot import upstream_api as lerobot_api
    from quackd.adapters.open_duck import upstream_api as open_duck_api
    from quackd.adapters.reachy_mini import upstream_api as reachy
    from quackd.adapters.rosbridge import upstream_api as rosbridge_api

    for name, api, backend, target in (
        ("reachy_mini", reachy, "sdk", "a robot"),
        ("lerobot", lerobot_api, "real", "an arm"),
        ("rosbridge", rosbridge_api, "ws", "a bridge"),
        ("open_duck", open_duck_api, "bridge", "a duck"),
    ):
        unverified = api.refs_by_status("UNVERIFIED")
        t = Table(
            title=f"{name} assumptions (UNVERIFIED: {len(unverified)}) — "
            f"see docs/adapters/{name}.md"
        )
        t.add_column("what")
        t.add_column("note")
        for ref in unverified:
            t.add_row(ref.name, ref.note)
        console.print(t)
        console.print(
            f"[dim]{name} upstream pinned at {api.PIN[:7]} (read {api.READ_ON}) · "
            f"VERIFIED refs: {len(api.refs_by_status('VERIFIED'))} · "
            f"the {backend} backend has never been run against {target}[/dim]"
        )
    return bool(ok)
