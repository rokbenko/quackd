"""The D-Bus objects a robot puts on the system bus for BlueZ: its advert and its GATT service.

BlueZ's peripheral API is objects the application exports and BlueZ calls into: an
`LEAdvertisement1` whose properties BlueZ reads once, when it is registered, and a GATT
application, a tree of `GattService1` and `GattCharacteristic1` objects that BlueZ finds through
`GetManagedObjects` (which dbus-fast answers for every object exported under a path) and serves
to centrals. Each class here is that interface and nothing else, transcribed from BlueZ's
doc/org.bluez.LEAdvertisement.rst, org.bluez.GattService.rst and org.bluez.GattCharacteristic.rst;
what a write means is `core.PeripheralCore`'s, because a method dbus-fast exports returns nothing
when Python calls it, so nothing worth testing can live in one.

The service has two characteristics: rx, which a central writes link frames to, with or without
response, and tx, which notifies the frames back. Notifying is setting tx's `Value` and emitting
PropertiesChanged, which BlueZ turns into a notification to every subscribed central.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from dbus_fast import DBusError, Variant
from dbus_fast.annotations import (
    DBusBool,
    DBusBytes,
    DBusDict,
    DBusObjectPath,
    DBusSignature,
    DBusStr,
    DBusUInt32,
)
from dbus_fast.constants import PropertyAccess
from dbus_fast.service import ServiceInterface, dbus_method, dbus_property

from quackd.passport.beacon import BLE_RX_UUID, BLE_SERVICE_UUID, BLE_TX_UUID
from quackd.radio.bluez.core import PeripheralCore, WriteRefused, advert_properties

DBusStrings = Annotated[list[str], DBusSignature("as")]

ADVERTISEMENT_IFACE = "org.bluez.LEAdvertisement1"
SERVICE_IFACE = "org.bluez.GattService1"
CHARACTERISTIC_IFACE = "org.bluez.GattCharacteristic1"

#: Where the objects live. The GATT application's root holds only the service, so what BlueZ's
#: GetManagedObjects of it returns is the service and its two characteristics and nothing else;
#: the advert is registered on its own, from beside it.
ROOT = "/org/quackd/passport"
APP_PATH = f"{ROOT}/app"
SERVICE_PATH = f"{APP_PATH}/service0"
RX_PATH = f"{SERVICE_PATH}/rx"
TX_PATH = f"{SERVICE_PATH}/tx"
ADVERT_PATH = f"{ROOT}/advert0"

READ = PropertyAccess.READ


def plain(value: Any) -> Any:
    """A value dbus-fast handed over with its variants opened."""
    if isinstance(value, Variant):
        return plain(value.value)
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    return value


class Advertisement(ServiceInterface):
    """`org.bluez.LEAdvertisement1`: a connectable advert of the passport's service data."""

    def __init__(self, service_data: bytes, on_release: Callable[[], None]) -> None:
        super().__init__(ADVERTISEMENT_IFACE)
        self.service_data = bytes(service_data)
        self.on_release = on_release

    def properties(self) -> dict[str, Any]:
        return advert_properties(self.service_data)

    @dbus_property(access=READ)
    def Type(self) -> DBusStr:
        return str(self.properties()["Type"])

    @dbus_property(access=READ)
    def ServiceData(self) -> DBusDict:
        return {
            uuid: Variant("ay", data) for uuid, data in self.properties()["ServiceData"].items()
        }

    @dbus_property(access=READ)
    def Includes(self) -> DBusStrings:
        return list(self.properties()["Includes"])

    @dbus_property(access=READ)
    def Discoverable(self) -> DBusBool:
        return bool(self.properties()["Discoverable"])

    @dbus_property(access=READ)
    def MinInterval(self) -> DBusUInt32:
        return int(self.properties()["MinInterval"])

    @dbus_property(access=READ)
    def MaxInterval(self) -> DBusUInt32:
        return int(self.properties()["MaxInterval"])

    # BlueZ's methods here return nothing, so none has a return annotation: dbus-fast reads one
    # as the D-Bus type of what the method returns
    @dbus_method()
    def Release(self):
        self.on_release()


class Service(ServiceInterface):
    """`org.bluez.GattService1`: the passport's primary service."""

    def __init__(self) -> None:
        super().__init__(SERVICE_IFACE)

    @dbus_property(access=READ)
    def UUID(self) -> DBusStr:
        return BLE_SERVICE_UUID

    @dbus_property(access=READ)
    def Primary(self) -> DBusBool:
        return True


class Rx(ServiceInterface):
    """`org.bluez.GattCharacteristic1` for rx: written by a central, with or without response."""

    def __init__(self, core: PeripheralCore) -> None:
        super().__init__(CHARACTERISTIC_IFACE)
        self.core = core

    @dbus_property(access=READ)
    def UUID(self) -> DBusStr:
        return BLE_RX_UUID

    @dbus_property(access=READ)
    def Service(self) -> DBusObjectPath:
        return SERVICE_PATH

    @dbus_property(access=READ)
    def Flags(self) -> DBusStrings:
        return ["write", "write-without-response"]

    @dbus_method()
    def WriteValue(self, value: DBusBytes, options: DBusDict):
        try:
            self.core.on_write(bytes(value), plain(options))
        except WriteRefused as e:
            raise DBusError(e.error, str(e)) from e


class Tx(ServiceInterface):
    """`org.bluez.GattCharacteristic1` for tx: notified to the centrals subscribed to it."""

    def __init__(self, core: PeripheralCore) -> None:
        super().__init__(CHARACTERISTIC_IFACE)
        self.core = core
        self._value = b""

    @dbus_property(access=READ)
    def UUID(self) -> DBusStr:
        return BLE_TX_UUID

    @dbus_property(access=READ)
    def Service(self) -> DBusObjectPath:
        return SERVICE_PATH

    @dbus_property(access=READ)
    def Flags(self) -> DBusStrings:
        return ["notify"]

    @dbus_property(access=READ)
    def Value(self) -> DBusBytes:
        return self._value

    @dbus_property(access=READ)
    def Notifying(self) -> DBusBool:
        return self.core.notifying

    @dbus_method()
    def StartNotify(self):
        self.core.start_notify()
        self.emit_properties_changed({"Notifying": True})

    @dbus_method()
    def StopNotify(self):
        self.core.stop_notify()
        self.emit_properties_changed({"Notifying": False})

    def notify(self, datagram: bytes) -> None:
        """One notification: the new value, and the signal BlueZ sends it on."""
        self._value = bytes(datagram)
        self.emit_properties_changed({"Value": self._value})


__all__ = [
    "ADVERT_PATH",
    "APP_PATH",
    "RX_PATH",
    "SERVICE_PATH",
    "TX_PATH",
    "Advertisement",
    "Rx",
    "Service",
    "Tx",
    "plain",
]
