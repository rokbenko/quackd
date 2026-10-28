"""What a robot's Bluetooth looks like, read without changing anything.

The radio asks this before it registers anything, and `quackd onboard preflight` shows it: is
bluetoothd on the bus at all, does BlueZ have the adapter, is it powered and unblocked, and can
it advertise and serve GATT. Everything comes from D-Bus and from /sys/class/rfkill, read only;
`core.py` turns the answers into a state and words.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from dbus_fast import BusType
from dbus_fast.aio import MessageBus

from quackd.radio import RadioState, RadioUnavailable
from quackd.radio.bluez.core import (
    BLUEZ,
    AdapterFacts,
    adapter_state,
    adapters_from,
    pick_adapter,
    rfkill_blocked,
)
from quackd.radio.bluez.peripheral import BlueZError, call

RFKILL = Path("/sys/class/rfkill")
DBUS = "org.freedesktop.DBus"


async def connect_bus(kind: Literal["system", "session"], address: str | None = None) -> MessageBus:
    """A connection to the system bus, or the session bus the tests stand a fake BlueZ on.
    `RadioUnavailable` when there is no bus, or it will not have this user."""
    bus_type = BusType.SYSTEM if kind == "system" else BusType.SESSION
    try:
        return await MessageBus(bus_address=address, bus_type=bus_type).connect()
    except PermissionError as e:
        raise RadioUnavailable(
            "denied", f"the {kind} bus refused this user: {e}", "sudo usermod -aG bluetooth $USER"
        ) from e
    except (OSError, ValueError, EOFError) as e:
        raise RadioUnavailable(
            "missing",
            f"there is no D-Bus {kind} bus to reach BlueZ on: {e}",
            "is dbus running? systemctl status dbus",
        ) from e


def read_rfkill(root: Path = RFKILL) -> list[dict[str, str]]:
    """Every rfkill switch, as the files in its directory say: `type`, `soft`, `hard`."""
    switches = []
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return []
    for entry in entries:
        switch = {}
        for name in ("type", "soft", "hard", "name"):
            try:
                switch[name] = (entry / name).read_text(encoding="utf-8").strip()
            except OSError:
                continue
        switches.append(switch)
    return switches


@dataclass(frozen=True)
class Probe:
    """What was found: a state, a sentence, a hint, and the adapter's facts when there is one."""

    state: RadioState
    detail: str
    hint: str | None
    adapter: AdapterFacts | None
    rfkill: bool | None


async def probe(bus: MessageBus, adapter: str, *, rfkill_root: Path = RFKILL) -> Probe:
    """bluetoothd, the adapter and its switches, as they are now."""
    try:
        [owned] = await call(
            bus, "/org/freedesktop/DBus", DBUS, "NameHasOwner", "s", [BLUEZ], destination=DBUS
        )
    except BlueZError as e:
        return Probe(
            "denied" if e.denied else "missing", f"the bus did not answer: {e}", None, None, None
        )
    if not owned:
        return Probe(
            "missing",
            "bluetoothd is not running: nothing owns org.bluez on the bus",
            "sudo systemctl enable --now bluetooth",
            None,
            None,
        )
    try:
        [objects] = await call(bus, "/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects")
    except BlueZError as e:
        state: RadioState = "denied" if e.denied else "missing"
        return Probe(state, f"BlueZ did not list its adapters: {e}", None, None, None)
    facts = pick_adapter(adapters_from(objects), adapter)
    blocked = rfkill_blocked(read_rfkill(rfkill_root))
    state, detail, hint = adapter_state(facts, adapter)
    if blocked:
        state, detail, hint = "off", "Bluetooth is blocked by rfkill", "rfkill unblock bluetooth"
    return Probe(state, detail, hint, facts, blocked)


__all__ = ["Probe", "connect_bus", "probe", "read_rfkill"]
