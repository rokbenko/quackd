"""The robot as a peripheral: its advert and its GATT service, registered with BlueZ.

BlueZ is asked for both over D-Bus, on the adapter's object: `GattManager1.RegisterApplication`
for the service tree and `LEAdvertisingManager1.RegisterAdvertisement` for the advert, each
naming objects this robot exports (`objects.py`). BlueZ reads an advert's properties once, when
it is registered, so changing one, as a rotation does, is unregistering it and registering it
again, which is why the radio takes a new advert at most every few seconds. Both registrations
die with this process's bus connection, so a crash leaves nothing registered behind it.

Written against BlueZ's documented API; not yet run against a real bluetoothd.
"""

from __future__ import annotations

import contextlib
from collections.abc import Sequence
from typing import Any

from dbus_fast import Message, MessageType
from dbus_fast.aio import MessageBus

from quackd.radio.bluez.core import (
    ADVERTISING_MANAGER_IFACE,
    BLUEZ,
    DEVICE_IFACE,
    GATT_MANAGER_IFACE,
    PeripheralCore,
)
from quackd.radio.bluez.objects import (
    ADVERT_PATH,
    APP_PATH,
    RX_PATH,
    SERVICE_PATH,
    TX_PATH,
    Advertisement,
    Rx,
    Service,
    Tx,
)


class BlueZError(Exception):
    """An error BlueZ, or the bus, answered a call with: its D-Bus error name and its text."""

    def __init__(self, name: str, text: str) -> None:
        super().__init__(f"{name}: {text}" if text else name)
        self.name = name
        self.text = text

    @property
    def denied(self) -> bool:
        return self.name.endswith(("AccessDenied", "NotPermitted", "NotAuthorized"))


async def call(
    bus: MessageBus,
    path: str,
    interface: str,
    member: str,
    signature: str = "",
    body: Sequence[Any] = (),
    *,
    destination: str = BLUEZ,
) -> list[Any]:
    """One method call, and its reply's body, or a `BlueZError` for an error reply."""
    reply = await bus.call(
        Message(
            destination=destination,
            path=path,
            interface=interface,
            member=member,
            signature=signature,
            body=list(body),
        )
    )
    if reply is None:
        raise BlueZError("org.freedesktop.DBus.Error.NoReply", f"{member} got no reply")
    if reply.message_type == MessageType.ERROR:
        text = str(reply.body[0]) if reply.body else ""
        raise BlueZError(str(reply.error_name), text)
    return list(reply.body)


class Peripheral:
    """This robot's advert and GATT service on one adapter."""

    def __init__(self, bus: MessageBus, adapter: str, core: PeripheralCore) -> None:
        self.bus = bus
        self.adapter_path = f"/org/bluez/{adapter}"
        self.core = core
        self.service, self.rx, self.tx = Service(), Rx(core), Tx(core)
        self.advert: Advertisement | None = None
        self.serving = False
        self.advertising = False
        #: set when BlueZ released the advert on its own, as it does when the adapter resets
        self.released = False

    async def serve(self) -> None:
        """Export the GATT service and register it with BlueZ."""
        for path, obj in ((SERVICE_PATH, self.service), (RX_PATH, self.rx), (TX_PATH, self.tx)):
            self.bus.export(path, obj)
        await call(
            self.bus,
            self.adapter_path,
            GATT_MANAGER_IFACE,
            "RegisterApplication",
            "oa{sv}",
            [APP_PATH, {}],
        )
        self.serving = True

    async def advertise(self, service_data: bytes) -> None:
        """Advertise `service_data`, replacing an advert already registered."""
        if self.advert is not None:
            await self.unadvertise()
        self.advert = Advertisement(service_data, on_release=self._released)
        self.bus.export(ADVERT_PATH, self.advert)
        self.released = False
        await call(
            self.bus,
            self.adapter_path,
            ADVERTISING_MANAGER_IFACE,
            "RegisterAdvertisement",
            "oa{sv}",
            [ADVERT_PATH, {}],
        )
        self.advertising = True

    def _released(self) -> None:
        self.released, self.advertising = True, False

    async def unadvertise(self) -> None:
        if self.advert is None:
            return
        if self.advertising:
            with contextlib.suppress(BlueZError):
                await call(
                    self.bus,
                    self.adapter_path,
                    ADVERTISING_MANAGER_IFACE,
                    "UnregisterAdvertisement",
                    "o",
                    [ADVERT_PATH],
                )
        self.bus.unexport(ADVERT_PATH)
        self.advert, self.advertising = None, False

    async def close(self) -> None:
        """Unregister both and take the objects off the bus."""
        await self.unadvertise()
        if self.serving:
            with contextlib.suppress(BlueZError):
                await call(
                    self.bus,
                    self.adapter_path,
                    GATT_MANAGER_IFACE,
                    "UnregisterApplication",
                    "o",
                    [APP_PATH],
                )
            self.serving = False
        for path in (TX_PATH, RX_PATH, SERVICE_PATH):
            self.bus.unexport(path)
        self.core.close_all("the radio stopped")

    def notify(self, datagram: bytes) -> None:
        self.tx.notify(datagram)

    async def disconnect(self, device: str) -> None:
        """Ask BlueZ to drop the central at `device`."""
        with contextlib.suppress(BlueZError):
            await call(self.bus, device, DEVICE_IFACE, "Disconnect")


__all__ = ["BlueZError", "Peripheral", "call"]
