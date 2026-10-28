"""`quackd onboard preflight`, the radio's half: what a robot's Bluetooth and LAN need, row by row.

Each row is one thing checked, whether it is fine, a warning or a failure, what was found, and
the one step that would change it. `rows` makes them from `Facts`, plain data, so every row and
its wording is tested on any platform; `collect` is the shell that gathers the facts, and it only
ever reads: the platform, which packages are installed, whether the system bus answers, what
BlueZ says of bluetoothd and the adapter, rfkill's switches, main.conf, the user's groups and
the LAN address. `live` is the one check that changes anything, and only when asked for: it
registers the advert and the GATT service for two seconds, then takes them down again.

The board itself (a Jetson, or `--board sim`) is lane C's `quackd.boards`, which hands its
verdict in as a row of its own; nothing here guesses what board it is on.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.metadata
import importlib.util
import os
import platform
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from quackd.radio import ONBOARD_INSTALL
from quackd.radio.bluez.core import AdapterFacts
from quackd.radio.bluez.privacy import MAIN_CONF, privacy_setting, privacy_words

Level = Literal["ok", "warn", "fail", "info"]
RadioChoice = Literal["bluez", "sim", "off"]

#: BlueZ releases that matter here: 5.55 is the oldest this is written to work with (JetPack 6's
#: Ubuntu 22.04 ships 5.64 and JetPack 7's 24.04 ships 5.72, and neither has been tried), and
#: 5.62 the first whose characteristics tell a central the link's MTU; before it bleak reports
#: 20, and every link this robot dials writes 20-byte datagrams.
BLUEZ_FLOOR = (5, 55)
BLUEZ_MTU = (5, 62)
PACKAGES = ("zeroconf", "openai", "bleak", "dbus-fast")
LINUX_ONLY = ("bleak", "dbus-fast")


@dataclass(frozen=True)
class Row:
    """One check: its name, its verdict, what was found, and what to do about it."""

    name: str
    level: Level
    detail: str
    hint: str | None = None


@dataclass
class Facts:
    """Everything preflight looks at, gathered by `collect` or written by a test."""

    system: str
    radio: RadioChoice = "bluez"
    #: the board check's own row, from quackd.boards; None when nobody made one
    board: Row | None = None
    #: each package's installed version, or None when it is not installed
    packages: dict[str, str | None] = field(default_factory=dict)
    #: "ok", or why the system bus could not be had: "missing" or "denied", with the reason
    bus: str = "ok"
    bus_detail: str = ""
    bluez_running: bool | None = None
    bluez_version: str | None = None
    adapter_name: str = "hci0"
    adapter: AdapterFacts | None = None
    rfkill: bool | None = None
    main_conf: str | None = None
    in_bluetooth_group: bool | None = None
    lan_address: str | None = None
    #: the sim air's directory and whether this user can write it
    air: str | None = None
    air_writable: bool | None = None
    live: Row | None = None


def _version(text: str | None) -> tuple[int, int] | None:
    match = re.search(r"(\d+)\.(\d+)", text or "")
    return (int(match.group(1)), int(match.group(2))) if match else None


def rows(facts: Facts) -> list[Row]:
    """Every check for the radio `facts.radio` names, in the order a person fixes them."""
    out: list[Row] = []
    if facts.board is not None:
        out.append(facts.board)
    out.append(_packages(facts))
    if facts.radio == "bluez":
        out += _bluez(facts)
    elif facts.radio == "sim":
        out.append(_air(facts))
    else:
        out.append(Row("radio", "info", "off, as asked: this robot meets others over its LAN only"))
    out.append(_lan(facts))
    if facts.live is not None:
        out.append(facts.live)
    return out


def _packages(facts: Facts) -> Row:
    wanted = [p for p in PACKAGES if facts.radio == "bluez" or p not in LINUX_ONLY]
    missing = [p for p in wanted if not facts.packages.get(p)]
    found = ", ".join(f"{p} {facts.packages[p]}" for p in wanted if facts.packages.get(p))
    if missing:
        return Row("onboard extra", "fail", f"{', '.join(missing)} not installed", ONBOARD_INSTALL)
    return Row("onboard extra", "ok", found)


def _bluez(facts: Facts) -> list[Row]:
    if facts.system != "Linux":
        return [
            Row(
                "bluetooth",
                "fail",
                f"BlueZ runs on Linux, and this is {facts.system}",
                "run it with --board sim to try it here",
            )
        ]
    out: list[Row] = []
    if facts.bus != "ok":
        hint = (
            "sudo usermod -aG bluetooth $USER, then log in again"
            if facts.bus == "denied"
            else "systemctl status dbus"
        )
        return [Row("system bus", "fail", facts.bus_detail or facts.bus, hint)]
    out.append(Row("system bus", "ok", "answers"))
    if not facts.bluez_running:
        return [
            *out,
            Row("bluetoothd", "fail", "not running", "sudo systemctl enable --now bluetooth"),
        ]
    version = _version(facts.bluez_version)
    if version is None:
        out.append(Row("bluetoothd", "warn", "running, version unknown"))
    elif version < BLUEZ_FLOOR:
        out.append(Row("bluetoothd", "fail", f"BlueZ {facts.bluez_version}, older than 5.55", None))
    elif version < BLUEZ_MTU:
        out.append(
            Row(
                "bluetoothd",
                "warn",
                f"BlueZ {facts.bluez_version}: before 5.62 it tells a central no link MTU, so "
                "links this robot dials write 20 bytes a datagram",
            )
        )
    else:
        out.append(Row("bluetoothd", "ok", f"BlueZ {facts.bluez_version}"))
    adapter = facts.adapter
    if adapter is None:
        return [
            *out,
            Row(
                "adapter",
                "fail",
                f"BlueZ has no {facts.adapter_name}",
                "bluetoothctl list, to see the adapters it has",
            ),
        ]
    if facts.rfkill:
        out.append(Row("rfkill", "fail", "Bluetooth is blocked", "rfkill unblock bluetooth"))
    if not adapter.powered:
        out.append(
            Row("adapter", "fail", f"{adapter.name} is powered off", "bluetoothctl power on")
        )
    else:
        roles = ", ".join(adapter.roles) if adapter.roles is not None else "roles not reported"
        out.append(Row("adapter", "ok", f"{adapter.name} {adapter.address}, {roles}"))
    if adapter.peripheral is False:
        out.append(
            Row(
                "peripheral role",
                "fail",
                "the controller cannot be a peripheral: no robot could link to it",
            )
        )
    if not adapter.advertising:
        out.append(Row("LE advertising", "fail", "BlueZ offers no LEAdvertisingManager1 here"))
    elif adapter.supported_instances == 0:
        out.append(Row("LE advertising", "fail", "no advertising instance is free"))
    else:
        instances = adapter.supported_instances
        free = f"{instances} instances" if instances is not None else "instances not reported"
        out.append(Row("LE advertising", "ok", f"{free}; 31 bytes a robot advertises"))
    out.append(
        Row("GATT server", "ok", "GattManager1 offered")
        if adapter.gatt
        else Row("GATT server", "fail", "BlueZ offers no GattManager1 here")
    )
    if facts.in_bluetooth_group is False:
        out.append(
            Row(
                "permission",
                "warn",
                "this user is not in the bluetooth group: a run in this terminal may be refused "
                "by D-Bus, though the service's unit adds the group",
                "sudo usermod -aG bluetooth $USER",
            )
        )
    out.append(
        Row(
            "advert interval",
            "info",
            "100 to 200 ms is asked for, which BlueZ honours only with experimental features "
            "on; otherwise it picks its own",
        )
    )
    setting = privacy_setting(facts.main_conf) if facts.main_conf is not None else None
    private, words = privacy_words(setting, adapter.address_type)
    out.append(
        Row(
            "address privacy",
            "ok" if private else "warn",
            words if facts.main_conf is not None else f"{MAIN_CONF} could not be read; {words}",
            None if private else "quackd onboard offers to turn it on",
        )
    )
    return out


def _air(facts: Facts) -> Row:
    if facts.air_writable:
        return Row(
            "sim air", "ok", f"{facts.air}: a simulated radio, shared by robots on this machine"
        )
    return Row(
        "sim air",
        "fail",
        f"{facts.air} cannot be written by this user",
        "pass --air with a directory this user can write",
    )


def _lan(facts: Facts) -> Row:
    zeroconf = bool(facts.packages.get("zeroconf"))
    if facts.radio == "sim":
        return Row("LAN", "ok", "the sim's own LAN, on this machine's loopback")
    if not facts.lan_address or facts.lan_address.startswith("127."):
        return Row(
            "LAN",
            "warn",
            "no network address: robots met will be met over Bluetooth only",
            None,
        )
    if not zeroconf:
        return Row(
            "LAN", "warn", f"{facts.lan_address}, with no zeroconf to announce on", ONBOARD_INSTALL
        )
    return Row("LAN", "ok", f"{facts.lan_address}, announced over DNS-SD")


def render(found: list[Row]) -> list[str]:
    """The rows as a person reads them: a verdict, a name and what was found, and under a row
    that is not fine the one step to take."""
    width = max((len(r.name) for r in found), default=0)
    lines = []
    for row in found:
        lines.append(f"{row.level:<4}  {row.name:<{width}}  {row.detail}")
        if row.hint and row.level != "ok":
            lines.append(f"{'':<4}  {'':<{width}}  -> {row.hint}")
    return lines


def as_json(found: list[Row]) -> list[dict[str, Any]]:
    return [asdict(r) for r in found]


def passed(found: list[Row]) -> bool:
    """Whether a robot can go on: nothing failed. Warnings are a person's to weigh."""
    return not any(r.level == "fail" for r in found)


# ── gathering the facts ────────────────────────────────────────────────────────────────────


def _installed() -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for name in PACKAGES:
        try:
            out[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            out[name] = None
    return out


def _bluez_version() -> str | None:
    for command in (["bluetoothctl", "--version"], ["bluetoothd", "--version"]):
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            done = subprocess.run(command, capture_output=True, text=True, timeout=3, check=False)
            if done.returncode == 0 and done.stdout.strip():
                return done.stdout.strip().split()[-1]
    return None


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _in_group(name: str) -> bool | None:
    """Whether this process is in the group `name`, or None where there are no such groups.
    Found by name rather than imported: `grp` and `os.getgroups` exist on Unix only, and this
    module is type-checked everywhere."""
    getgroups = getattr(os, "getgroups", None)
    try:
        gid = importlib.import_module("grp").getgrnam(name).gr_gid
    except (ImportError, KeyError):
        return None
    return None if getgroups is None else gid in getgroups()


async def collect(
    *,
    radio: RadioChoice = "bluez",
    adapter: str = "hci0",
    board: Row | None = None,
    air: Path | None = None,
    lan_address: str | None = None,
    live: bool = False,
) -> Facts:
    """The facts for `rows`, read off this machine. Changes nothing unless `live`."""
    facts = Facts(
        system=platform.system(),
        radio=radio,
        board=board,
        packages=_installed(),
        adapter_name=adapter,
        lan_address=lan_address,
    )
    if radio == "sim" and air is not None:
        facts.air = str(air)
        facts.air_writable = _writable(air)
    if radio != "bluez" or facts.system != "Linux":
        return facts
    if importlib.util.find_spec("dbus_fast") is None:
        facts.bus, facts.bus_detail = "missing", "dbus-fast is not installed"
        return facts
    from quackd.radio import RadioUnavailable
    from quackd.radio.bluez.probe import connect_bus, probe

    try:
        bus = await connect_bus("system")
    except RadioUnavailable as e:
        facts.bus = "denied" if e.state == "denied" else "missing"
        facts.bus_detail = e.detail
        return facts
    try:
        found = await probe(bus, adapter)
        facts.bluez_running, facts.adapter, facts.rfkill = (
            found.running,
            found.adapter,
            found.rfkill,
        )
    finally:
        bus.disconnect()
    facts.bluez_version = await asyncio.to_thread(_bluez_version)
    facts.main_conf = await asyncio.to_thread(_read, Path(MAIN_CONF))
    facts.in_bluetooth_group = _in_group("bluetooth")
    if live:
        facts.live = await live_check(adapter)
    return facts


def _writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / f".preflight-{os.getpid()}"
        probe.write_bytes(b"")
        probe.unlink()
    except OSError:
        return False
    return True


async def live_check(adapter: str, *, hold_s: float = 2.0) -> Row:
    """Register the advert and the GATT service for `hold_s`, then take both down: the one
    preflight check that asks BlueZ for anything, and only with --live."""
    from quackd.passport.beacon import ble_service_data, new_beacon_id
    from quackd.radio.bluez.radio import BlueZRadio

    radio = BlueZRadio(adapter=adapter, scan=False)
    status = await radio.start(
        advert=ble_service_data(new_beacon_id(), lan=False, link=True),
        on_sighting=lambda s: None,
        on_link=lambda p, a: None,
    )
    try:
        await asyncio.sleep(hold_s)
    finally:
        await radio.stop()
    if status.state == "ready" and status.advertising and status.accepting:
        return Row("live", "ok", f"BlueZ took the advert and the GATT service for {hold_s:g} s")
    return Row("live", "fail", f"BlueZ would not take both: {status.detail}", status.hint)


__all__ = [
    "Facts",
    "Row",
    "as_json",
    "collect",
    "live_check",
    "passed",
    "render",
    "rows",
]
