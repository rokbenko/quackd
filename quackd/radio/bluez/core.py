"""The BlueZ radio's logic with no D-Bus and no bleak in it, so every platform tests it.

BlueZ hands a GATT server its writes as D-Bus method calls and sends its notifications when a
characteristic's `Value` property changes; bleak hands a central its notifications as callbacks
and takes its writes as calls. Everything between those calls and a `quackd.radio.Pipe` is here:
which central a write came from, the size it may be, a pipe per central, the notifications that
go to every subscribed central at once, a client pipe over anything shaped like a `BleakClient`,
a sighting made from what bleak reports of an advert, and what BlueZ's object tree says about an
adapter. The D-Bus shims in `objects.py` and the bleak calls in `central.py` stay thin on top.

None of this has run against a real controller.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from quackd.passport.beacon import (
    BLE_SERVICE_UUID,
    advertising_data,
    read_ble_service_data,
)
from quackd.passport.link import MAX_DATAGRAM, MAX_LINKS, MIN_DATAGRAM
from quackd.radio import PipeClosed, RadioState, Sighting
from quackd.radio.pipe import BasePipe

BLUEZ = "org.bluez"
ADAPTER_IFACE = "org.bluez.Adapter1"
DEVICE_IFACE = "org.bluez.Device1"
GATT_MANAGER_IFACE = "org.bluez.GattManager1"
ADVERTISING_MANAGER_IFACE = "org.bluez.LEAdvertisingManager1"
#: The ATT MTU before any exchange: a write of up to 20 bytes is all a link is sure of.
DEFAULT_MTU = 23
#: Centrals the GATT server keeps a pipe for at once: every link a robot may hold, and as many
#: again still opening one, past which a write from a new central is refused.
MAX_CENTRALS = 2 * MAX_LINKS
#: The pause after each notification. BlueZ queues what it is told to notify, and a hello of
#: 16 KB is about 95 notifications at an MTU of 185, so they are spread out rather than dumped;
#: the figure is a guess at what a controller drains in a connection interval, not a measurement.
NOTIFY_PACE_S = 0.005
#: How long a central waits for BlueZ to settle the write size above the default it starts
#: from: until the MTU exchange has finished, bleak reports 20.
SETTLE_S = 1.0
_DEVICE = re.compile(r"^/org/bluez/(hci[0-9]+)/dev_([0-9A-F]{2}(?:_[0-9A-F]{2}){5})$")
_ADDRESS = re.compile(r"^[0-9A-F]{2}(?::[0-9A-F]{2}){5}$")

#: BlueZ's error names, which a GATT server answers a refused write with.
INVALID_LENGTH = "org.bluez.Error.InvalidValueLength"
NOT_SUPPORTED = "org.bluez.Error.NotSupported"
NOT_PERMITTED = "org.bluez.Error.NotPermitted"
FAILED = "org.bluez.Error.Failed"


def address_of(device_path: str) -> str | None:
    """The Bluetooth address a BlueZ device path names, `AA:BB:CC:DD:EE:FF`, or None."""
    match = _DEVICE.match(device_path)
    return match.group(2).replace("_", ":") if match else None


def device_path(adapter: str, address: str) -> str:
    """The BlueZ object path of the device at `address` on `adapter` (`hci0`)."""
    if not _ADDRESS.match(address.upper()):
        raise ValueError(f"{address!r} is not a Bluetooth address")
    return f"/org/bluez/{adapter}/dev_{address.upper().replace(':', '_')}"


# ── the advert BlueZ is asked to send ──────────────────────────────────────────────────────


#: The intervals asked for, in milliseconds: 100 to 200 ms keeps a robot heard five to ten
#: times a second, which proximity wants, at the cost of airtime a phone would not spend. BlueZ
#: reads these only when bluetoothd runs with experimental features, and otherwise picks its own.
MIN_INTERVAL_MS = 100
MAX_INTERVAL_MS = 200


def advert_properties(service_data: bytes) -> dict[str, Any]:
    """What the `LEAdvertisement1` object says, as plain values: a connectable advert with the
    passport's service data and nothing else, no name, no TX power, no secondary channel, so
    that with the flags BlueZ adds it is the 31 bytes of a legacy advert and no more."""
    return {
        "Type": "peripheral",
        "ServiceData": {BLE_SERVICE_UUID: bytes(service_data)},
        "Includes": [],
        "Discoverable": True,
        "MinInterval": MIN_INTERVAL_MS,
        "MaxInterval": MAX_INTERVAL_MS,
    }


def advert_bytes(properties: Mapping[str, Any]) -> bytes:
    """The advert BlueZ builds from `properties`: its flags AD, then the service data. What a
    test holds to the 31-byte budget, since BlueZ refuses a longer one, or moves it to a scan
    response nobody asked for."""
    if properties.get("Includes") or properties.get("LocalName") or "TxPower" in properties:
        raise ValueError("a passport advert carries nothing BlueZ would add to it")
    data = properties["ServiceData"][BLE_SERVICE_UUID]
    return advertising_data(bytes(data), discoverable=bool(properties.get("Discoverable")))


# ── the GATT server: a pipe per central ────────────────────────────────────────────────────


class WriteRefused(Exception):
    """A write the rx characteristic will not take, with the BlueZ error it is answered with."""

    def __init__(self, error: str, message: str) -> None:
        super().__init__(message)
        self.error = error


class ServerPipe(BasePipe):
    """One central's link to this robot, as the GATT server sees it: what the central writes to
    rx arrives here, and what this end sends is notified on tx. A notification reaches every
    subscribed central, not only this one, which is why link frames carry a tag."""

    def __init__(self, core: PeripheralCore, device: str, datagram_size: int) -> None:
        super().__init__(datagram_size)
        self.core = core
        self.device = device
        #: notifications sent while no central was subscribed, which went nowhere
        self.unheard = 0

    async def send(self, datagram: bytes, *, barrier: bool = True) -> None:
        if self.closed is not None:
            raise PipeClosed(self.closed)
        if len(datagram) > self.datagram_size:
            raise ValueError(f"{len(datagram)} bytes, over this link's {self.datagram_size}")
        if not self.core.notifying:
            self.unheard += 1
        else:
            self.core.notify(datagram)
        await asyncio.sleep(self.core.pace_s)

    async def close(self) -> None:
        self.core.drop(self.device, "closed by this end", disconnect=True)


class PeripheralCore:
    """The GATT server's state: which centrals have written, their pipes, and whether anybody
    is subscribed to tx. `notify` sends one notification (the tx object's `Value` change);
    `on_pipe` hears of each new central's pipe; `disconnect` asks BlueZ to drop a central."""

    def __init__(
        self,
        *,
        notify: Callable[[bytes], None],
        on_pipe: Callable[[ServerPipe, str], None],
        disconnect: Callable[[str], None] | None = None,
        max_centrals: int = MAX_CENTRALS,
        pace_s: float = NOTIFY_PACE_S,
    ) -> None:
        self.notify = notify
        self.on_pipe = on_pipe
        self.disconnect = disconnect
        self.max_centrals = max_centrals
        self.pace_s = pace_s
        self.notifying = False
        self.pipes: dict[str, ServerPipe] = {}

    def start_notify(self) -> None:
        self.notifying = True

    def stop_notify(self) -> None:
        self.notifying = False

    def on_write(self, value: bytes, options: Mapping[str, Any]) -> None:
        """A central wrote `value` to rx with BlueZ's `options` (plain values: `device`, `mtu`,
        `type`, `offset`). Raises `WriteRefused` for a write a passport link never makes."""
        if options.get("offset", 0):
            raise WriteRefused(NOT_SUPPORTED, "a passport link writes whole datagrams, at offset 0")
        if options.get("type") == "reliable" or options.get("prepare-authorize"):
            raise WriteRefused(NOT_SUPPORTED, "a passport link takes no reliable or prepared write")
        device = options.get("device")
        if not isinstance(device, str) or not device:
            raise WriteRefused(FAILED, "a write that names no device")
        mtu = options.get("mtu")
        size = (int(mtu) if isinstance(mtu, int) and mtu > 0 else DEFAULT_MTU) - 3
        size = max(MIN_DATAGRAM, min(size, MAX_DATAGRAM))
        if len(value) > size:
            raise WriteRefused(INVALID_LENGTH, f"{len(value)} bytes, over the {size} this link has")
        pipe = self.pipes.get(device)
        if pipe is None:
            if len(self.pipes) >= self.max_centrals:
                raise WriteRefused(
                    NOT_PERMITTED, "this robot is linked to as many robots as it can"
                )
            pipe = self.pipes[device] = ServerPipe(self, device, size)
            pipe.on_close(lambda _reason: self._forget(device, pipe))
            self.on_pipe(pipe, address_of(device) or device)
        pipe._received(bytes(value))

    def _forget(self, device: str, pipe: ServerPipe) -> None:
        if self.pipes.get(device) is pipe:
            del self.pipes[device]

    def drop(self, device: str, reason: str, *, disconnect: bool = False) -> None:
        """End the pipe of the central at `device`: it disconnected, or this end closed it, in
        which case BlueZ is asked to disconnect it too."""
        pipe = self.pipes.pop(device, None)
        if pipe is not None:
            pipe._ended(reason)
        if disconnect and self.disconnect is not None:
            with contextlib.suppress(Exception):
                self.disconnect(device)

    def device_gone(self, device: str) -> None:
        self.drop(device, "link lost: the central disconnected")

    def close_all(self, reason: str) -> None:
        for device in list(self.pipes):
            self.drop(device, reason)


# ── the central: a pipe over a connected client ────────────────────────────────────────────


class GattClient(Protocol):
    """The part of a `bleak.BleakClient` a client pipe uses."""

    async def write_gatt_char(
        self, char_specifier: Any, data: Any, response: bool | None = ...
    ) -> None: ...

    async def stop_notify(self, char_specifier: Any) -> None: ...

    async def disconnect(self) -> None: ...


class ClientPipe(BasePipe):
    """This robot's end of a link it dialled: datagrams written to the other robot's rx, a write
    request for a barrier and a write command for the rest, and its tx notifications in. The
    pipe is made, and `received` wired to the notification callback, before the link's OPEN."""

    def __init__(self, client: GattClient, rx: Any, tx: Any, datagram_size: int) -> None:
        super().__init__(datagram_size)
        self.client, self.rx, self.tx = client, rx, tx

    def received(self, datagram: bytes | bytearray) -> None:
        self._received(bytes(datagram))

    def lost(self, reason: str = "link lost: disconnected") -> None:
        self._ended(reason)

    async def send(self, datagram: bytes, *, barrier: bool = True) -> None:
        if self.closed is not None:
            raise PipeClosed(self.closed)
        if len(datagram) > self.datagram_size:
            raise ValueError(f"{len(datagram)} bytes, over this link's {self.datagram_size}")
        try:
            await self.client.write_gatt_char(self.rx, datagram, response=barrier)
        except Exception as e:  # bleak raises its own errors, and D-Bus's under them
            self._ended(f"link lost: {e}")
            raise PipeClosed(f"link lost: {e}") from e

    async def close(self) -> None:
        if not self._ended("closed by this end"):
            return
        with contextlib.suppress(Exception):
            await self.client.stop_notify(self.tx)
        with contextlib.suppress(Exception):
            await self.client.disconnect()


async def settled_write_size(read: Callable[[], int], *, within_s: float = SETTLE_S) -> int:
    """The write size a link will have: what `read` says once it stops saying the default 20,
    or 20 after `within_s`, never more than a datagram may be."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + within_s
    size = read()
    while size <= MIN_DATAGRAM and loop.time() < deadline:
        await asyncio.sleep(0.1)
        size = read()
    return max(MIN_DATAGRAM, min(size, MAX_DATAGRAM))


# ── what the scanner reports ───────────────────────────────────────────────────────────────


def sighting_from(
    address: str, rssi: float | None, service_data: Mapping[str, bytes], at: float
) -> Sighting | None:
    """A sighting from what bleak reports of one advert, or None when it is not a passport
    advert, or carries a strength that is none: HCI reports 127 when it has no RSSI to give."""
    data = service_data.get(BLE_SERVICE_UUID)
    beacon = read_ble_service_data(bytes(data)) if data else None
    if beacon is None or rssi is None or not -127 <= rssi <= 20:
        return None
    return Sighting(beacon.beacon_id, beacon.flags, float(rssi), address, at)


# ── what BlueZ's object tree says about an adapter ─────────────────────────────────────────


@dataclass(frozen=True)
class AdapterFacts:
    """One adapter as `GetManagedObjects` describes it: enough to say whether it can advertise,
    serve GATT and scan, and what address it shows the air."""

    path: str
    address: str | None
    address_type: str | None
    alias: str | None
    powered: bool
    roles: tuple[str, ...] | None
    advertising: bool
    supported_instances: int | None
    gatt: bool

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]

    @property
    def peripheral(self) -> bool | None:
        """Whether it can take the peripheral role; None when this BlueZ does not say."""
        return None if self.roles is None else "peripheral" in self.roles


def _plain(value: Any) -> Any:
    """A D-Bus value with its variants opened, as dbus-fast hands them over or a test writes
    them: anything with a `.value` and a `.signature` is a variant."""
    if hasattr(value, "value") and hasattr(value, "signature"):
        return _plain(value.value)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    return value


def adapters_from(objects: Mapping[str, Mapping[str, Mapping[str, Any]]]) -> list[AdapterFacts]:
    """Every adapter in BlueZ's object tree, in path order."""
    found = []
    for path, interfaces in sorted(objects.items()):
        if ADAPTER_IFACE not in interfaces:
            continue
        adapter = _plain(interfaces[ADAPTER_IFACE])
        advertising = _plain(interfaces.get(ADVERTISING_MANAGER_IFACE, {}))
        roles = adapter.get("Roles")
        instances = advertising.get("SupportedInstances")
        found.append(
            AdapterFacts(
                path=str(path),
                address=adapter.get("Address"),
                address_type=adapter.get("AddressType"),
                alias=adapter.get("Alias"),
                powered=bool(adapter.get("Powered")),
                roles=tuple(str(r) for r in roles) if isinstance(roles, list) else None,
                advertising=ADVERTISING_MANAGER_IFACE in interfaces,
                supported_instances=int(instances) if isinstance(instances, int) else None,
                gatt=GATT_MANAGER_IFACE in interfaces,
            )
        )
    return found


def pick_adapter(adapters: list[AdapterFacts], name: str) -> AdapterFacts | None:
    """The adapter called `name` (`hci0`), or None."""
    return next((a for a in adapters if a.name == name), None)


def adapter_state(adapter: AdapterFacts | None, name: str) -> tuple[RadioState, str, str | None]:
    """What an adapter's facts mean for a robot's radio: a state, a sentence and a hint."""
    if adapter is None:
        return (
            "missing",
            f"BlueZ has no adapter called {name}",
            "check the board has Bluetooth and that bluetoothd sees it: bluetoothctl list",
        )
    if not adapter.powered:
        return "off", f"{name} is powered off", "bluetoothctl power on"
    if adapter.peripheral is False:
        return (
            "unsupported",
            f"{name} cannot take the peripheral role, so other robots could not link to it",
            None,
        )
    if not adapter.advertising or not adapter.gatt:
        missing = [
            n for n, ok in (("advertising", adapter.advertising), ("GATT", adapter.gatt)) if not ok
        ]
        return "degraded", f"{name} offers no {' or '.join(missing)} manager", None
    if adapter.supported_instances == 0:
        return "degraded", f"{name} has no advertising instance free", None
    return "ready", f"{name} at {adapter.address} ({adapter.address_type} address)", None


def rfkill_blocked(entries: Sequence[Mapping[str, str]]) -> bool | None:
    """Whether Bluetooth is blocked by rfkill, from `/sys/class/rfkill/*` (`type`, `soft`,
    `hard`), or None when no Bluetooth switch is listed."""
    switches = [e for e in entries if e.get("type", "").strip() == "bluetooth"]
    if not switches:
        return None
    return any("1" in (e.get("soft", "").strip(), e.get("hard", "").strip()) for e in switches)


__all__ = [
    "BLUEZ",
    "DEFAULT_MTU",
    "MAX_CENTRALS",
    "AdapterFacts",
    "ClientPipe",
    "GattClient",
    "PeripheralCore",
    "ServerPipe",
    "WriteRefused",
    "adapter_state",
    "adapters_from",
    "address_of",
    "advert_bytes",
    "advert_properties",
    "device_path",
    "pick_adapter",
    "rfkill_blocked",
    "settled_write_size",
    "sighting_from",
]
