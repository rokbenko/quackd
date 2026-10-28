"""The objects a robot hands BlueZ, held to BlueZ's documented interfaces, member by member.

`BLUEZ_DOCS` is transcribed from BlueZ's doc/org.bluez.LEAdvertisement.rst,
org.bluez.GattService.rst and org.bluez.GattCharacteristic.rst: for every property this robot
offers, the type and access the documentation gives it, and for every method its argument types
in and out. dbus-fast builds each object's introspection from its annotations, which is what
BlueZ reads, so a type spelt wrong here is a registration BlueZ would refuse on a robot.

Needs dbus-fast, which the onboard extra installs on Linux; the CI job `onboard` runs this with
QUACKD_REQUIRE_DBUS=1, so a missing package fails there instead of skipping.
"""

from __future__ import annotations

import importlib.util
import os

import pytest

if importlib.util.find_spec("dbus_fast") is None:
    if os.environ.get("QUACKD_REQUIRE_DBUS") == "1":
        raise RuntimeError("QUACKD_REQUIRE_DBUS=1 and dbus-fast is not installed")
    pytest.skip("dbus-fast comes with the onboard extra, on Linux", allow_module_level=True)

from dbus_fast.constants import PropertyAccess

from quackd.passport.beacon import BLE_RX_UUID, BLE_SERVICE_UUID, BLE_TX_UUID
from quackd.radio.bluez.core import PeripheralCore
from quackd.radio.bluez.objects import (
    SERVICE_PATH,
    Advertisement,
    Rx,
    Service,
    Tx,
)

R = "read"
#: Interface, then each property's (type, access) and each method's (in, out) types.
BLUEZ_DOCS = {
    "advert": (
        "org.bluez.LEAdvertisement1",
        {
            "Type": ("s", R),
            "ServiceData": ("a{sv}", R),
            "Includes": ("as", R),
            "Discoverable": ("b", R),
            "MinInterval": ("u", R),
            "MaxInterval": ("u", R),
        },
        {"Release": ("", "")},
    ),
    "service": (
        "org.bluez.GattService1",
        {"UUID": ("s", R), "Primary": ("b", R)},
        {},
    ),
    "rx": (
        "org.bluez.GattCharacteristic1",
        {"UUID": ("s", R), "Service": ("o", R), "Flags": ("as", R)},
        {"WriteValue": ("aya{sv}", "")},
    ),
    "tx": (
        "org.bluez.GattCharacteristic1",
        {
            "UUID": ("s", R),
            "Service": ("o", R),
            "Flags": ("as", R),
            "Value": ("ay", R),
            "Notifying": ("b", R),
        },
        {"StartNotify": ("", ""), "StopNotify": ("", "")},
    ),
}


def objects() -> dict[str, object]:
    core = PeripheralCore(notify=lambda d: None, on_pipe=lambda p, a: None)
    return {
        "advert": Advertisement(bytes(10), on_release=lambda: None),
        "service": Service(),
        "rx": Rx(core),
        "tx": Tx(core),
    }


@pytest.mark.parametrize("which", sorted(BLUEZ_DOCS))
def test_each_object_is_the_interface_bluez_documents(which: str) -> None:
    name, properties, methods = BLUEZ_DOCS[which]
    interface = objects()[which].introspect()  # type: ignore[attr-defined]
    assert interface.name == name
    got = {p.name: (p.signature, p.access.value) for p in interface.properties}
    assert got == properties
    assert all(p.access == PropertyAccess.READ for p in interface.properties)
    got_methods = {m.name: (m.in_signature, m.out_signature) for m in interface.methods}
    assert got_methods == methods


def test_the_service_and_its_characteristics_name_the_passport_uuids() -> None:
    made = objects()
    assert made["service"].UUID == BLE_SERVICE_UUID and made["service"].Primary is True  # type: ignore[attr-defined]
    assert made["rx"].UUID == BLE_RX_UUID and made["rx"].Service == SERVICE_PATH  # type: ignore[attr-defined]
    assert made["rx"].Flags == ["write", "write-without-response"]  # type: ignore[attr-defined]
    assert made["tx"].UUID == BLE_TX_UUID and made["tx"].Flags == ["notify"]  # type: ignore[attr-defined]
    advert = made["advert"]
    assert advert.Type == "peripheral" and advert.Includes == []  # type: ignore[attr-defined]
    [(uuid, data)] = advert.ServiceData.items()  # type: ignore[attr-defined]
    assert uuid == BLE_SERVICE_UUID and data.signature == "ay" and data.value == bytes(10)
