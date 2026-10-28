"""The BlueZ radio against a fake BlueZ on a private bus: what it registers, what BlueZ would read
of it, and a central's writes and notifications going through D-Bus as BlueZ would carry them.

The fake owns `org.bluez` on a session bus that dbus-run-session starts for the CI job `onboard`,
and plays BlueZ's part: an adapter, the GATT and advertising managers recording what they are
asked to register, then a central writing link frames to rx and reading tx's notifications as
PropertiesChanged signals, and a device and bluetoothd going away. bleak never runs here (its
scanner is off), because it talks to the system bus's real BlueZ; the central half is untested
until a robot.

Needs dbus-fast and a session bus. Skips without either, unless QUACKD_REQUIRE_DBUS=1.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import time
from collections.abc import AsyncIterator, Callable
from typing import Annotated, Any

import pytest

if importlib.util.find_spec("dbus_fast") is None or not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
    if os.environ.get("QUACKD_REQUIRE_DBUS") == "1":
        raise RuntimeError("QUACKD_REQUIRE_DBUS=1, and there is no dbus-fast or no session bus")
    pytest.skip("needs dbus-fast and a session bus: dbus-run-session", allow_module_level=True)

from dbus_fast import BusType, Message, Variant
from dbus_fast.aio import MessageBus
from dbus_fast.annotations import (
    DBusBool,
    DBusByte,
    DBusDict,
    DBusObjectPath,
    DBusSignature,
    DBusStr,
)
from dbus_fast.constants import PropertyAccess
from dbus_fast.service import ServiceInterface, dbus_method, dbus_property

import quackd.radio.bluez.radio as bluez_radio
from quackd.passport.beacon import (
    BLE_RX_UUID,
    BLE_SERVICE_UUID,
    BLE_TX_UUID,
    LEGACY_AD_BYTES,
    ble_service_data,
    new_beacon_id,
)
from quackd.passport.link import (
    PERIPHERAL_BIT,
    Accept,
    Data,
    Limits,
    Media,
    Open,
    Reassembler,
    Role,
    decode,
    encode,
    new_tag,
    split,
)
from quackd.passport.link import (
    Message as LinkMessage,
)
from quackd.radio import Pipe
from quackd.radio.bluez.core import INVALID_LENGTH, NOT_SUPPORTED
from quackd.radio.bluez.objects import (
    ADVERT_PATH,
    APP_PATH,
    RX_PATH,
    SERVICE_PATH,
    TX_PATH,
)
from quackd.radio.bluez.peripheral import BlueZError, call
from quackd.radio.bluez.radio import BlueZRadio
from quackd.radio.links import Link, LinkTable

DBusStrings = Annotated[list[str], DBusSignature("as")]
ADAPTER = "/org/bluez/hci0"
DEVICE = f"{ADAPTER}/dev_11_22_33_44_55_66"
CHARACTERISTIC = "org.bluez.GattCharacteristic1"
DBUS = "org.freedesktop.DBus"
READ = PropertyAccess.READ


class Registry:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []


class FakeAdapter(ServiceInterface):
    def __init__(self) -> None:
        super().__init__("org.bluez.Adapter1")

    @dbus_property(access=READ)
    def Address(self) -> DBusStr:
        return "AA:BB:CC:00:00:01"

    @dbus_property(access=READ)
    def AddressType(self) -> DBusStr:
        return "public"

    @dbus_property(access=READ)
    def Powered(self) -> DBusBool:
        return True

    @dbus_property(access=READ)
    def Roles(self) -> DBusStrings:
        return ["central", "peripheral"]


class FakeGattManager(ServiceInterface):
    def __init__(self, registry: Registry) -> None:
        super().__init__("org.bluez.GattManager1")
        self.registry = registry

    @dbus_method()
    def RegisterApplication(self, application: DBusObjectPath, options: DBusDict):
        self.registry.calls.append(("RegisterApplication", application))

    @dbus_method()
    def UnregisterApplication(self, application: DBusObjectPath):
        self.registry.calls.append(("UnregisterApplication", application))


class FakeAdvertisingManager(ServiceInterface):
    def __init__(self, registry: Registry) -> None:
        super().__init__("org.bluez.LEAdvertisingManager1")
        self.registry = registry

    @dbus_property(access=READ)
    def SupportedInstances(self) -> DBusByte:
        return 4

    @dbus_method()
    def RegisterAdvertisement(self, advertisement: DBusObjectPath, options: DBusDict):
        self.registry.calls.append(("RegisterAdvertisement", advertisement))

    @dbus_method()
    def UnregisterAdvertisement(self, advertisement: DBusObjectPath):
        self.registry.calls.append(("UnregisterAdvertisement", advertisement))


async def until(condition: Callable[[], object], within_s: float = 5.0) -> None:
    deadline = time.monotonic() + within_s
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("the condition never came true")
        await asyncio.sleep(0.02)


@pytest.fixture
async def bluez() -> AsyncIterator[tuple[MessageBus, Registry]]:
    bus = await MessageBus(bus_type=BusType.SESSION).connect()
    registry = Registry()
    bus.export(ADAPTER, FakeAdapter())
    bus.export(ADAPTER, FakeGattManager(registry))
    bus.export(ADAPTER, FakeAdvertisingManager(registry))
    await bus.request_name("org.bluez")
    yield bus, registry
    bus.disconnect()


def echo(body: bytes, media: Media) -> tuple[int, bytes | None, Media]:
    return 200, b"echo:" + body, media


async def test_the_radio_registers_a_31_byte_advert_and_a_passport_gatt_service(
    bluez: tuple[MessageBus, Registry],
) -> None:
    fake, registry = bluez
    beacon = new_beacon_id()
    radio = BlueZRadio(bus="session", scan=False)
    status = await radio.start(
        advert=ble_service_data(beacon, lan=False, link=True),
        on_sighting=lambda s: None,
        on_link=lambda pipe, address: None,
    )
    try:
        assert status.state == "ready", status.detail
        assert status.advertising and status.accepting and not status.scanning
        assert (status.address, status.address_type) == ("AA:BB:CC:00:00:01", "public")
        assert registry.calls == [
            ("RegisterApplication", APP_PATH),
            ("RegisterAdvertisement", ADVERT_PATH),
        ]
        name = radio._bus.unique_name  # type: ignore[union-attr]
        # what BlueZ walks after RegisterApplication: the service and its two characteristics
        [managed] = await call(
            fake, APP_PATH, f"{DBUS}.ObjectManager", "GetManagedObjects", destination=name
        )
        assert set(managed) == {SERVICE_PATH, RX_PATH, TX_PATH}
        uuids = {
            path: ifaces[next(iter(i for i in ifaces if "Gatt" in i))]["UUID"].value
            for path, ifaces in managed.items()
        }
        assert uuids == {SERVICE_PATH: BLE_SERVICE_UUID, RX_PATH: BLE_RX_UUID, TX_PATH: BLE_TX_UUID}
        # what BlueZ reads of the advert, and the advert it would build from it
        [props] = await call(
            fake,
            ADVERT_PATH,
            f"{DBUS}.Properties",
            "GetAll",
            "s",
            ["org.bluez.LEAdvertisement1"],
            destination=name,
        )
        assert props["Type"].value == "peripheral" and props["Includes"].value == []
        assert not {"LocalName", "TxPower", "ManufacturerData", "ServiceUUIDs"} & set(props)
        [(uuid, data)] = props["ServiceData"].value.items()
        assert uuid == BLE_SERVICE_UUID and data.value == ble_service_data(
            beacon, lan=False, link=True
        )
        flags_ad = 3  # BlueZ's own, for a discoverable peripheral
        service_ad = 1 + 1 + 16 + len(data.value)
        assert flags_ad + service_ad == LEGACY_AD_BYTES
    finally:
        await radio.stop()
    assert ("UnregisterAdvertisement", ADVERT_PATH) in registry.calls
    assert ("UnregisterApplication", APP_PATH) in registry.calls


async def test_a_central_links_through_bluez_and_is_dropped_when_bluez_says_it_left(
    bluez: tuple[MessageBus, Registry], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, registry = bluez
    beacon, central_beacon = new_beacon_id(), new_beacon_id()
    table = LinkTable()
    tasks: set[asyncio.Task[Link | None]] = set()

    def on_link(pipe: Pipe, address: str) -> None:
        assert address == "11:22:33:44:55:66"
        task = asyncio.get_running_loop().create_task(
            table.accept(pipe, me=beacon, inbox_for=lambda route: echo)
        )
        tasks.add(task)

    radio = BlueZRadio(bus="session", scan=False)
    await radio.start(
        advert=ble_service_data(beacon, lan=False, link=True),
        on_sighting=lambda s: None,
        on_link=on_link,
    )
    name = radio._bus.unique_name  # type: ignore[union-attr]
    notified: list[bytes] = []

    def heard(msg: Message) -> bool:
        if msg.member == "PropertiesChanged" and msg.path == TX_PATH:
            value = msg.body[1].get("Value")
            if value is not None:
                notified.append(bytes(value.value))
        return False

    rule = f"type='signal',sender='{name}',path='{TX_PATH}',interface='{DBUS}.Properties'"
    await call(fake, "/org/freedesktop/DBus", DBUS, "AddMatch", "s", [rule], destination=DBUS)
    fake.add_message_handler(heard)

    async def write(data: bytes, kind: str = "request", **extra: Any) -> None:
        options = {
            "device": Variant("o", DEVICE),
            "mtu": Variant("q", 185),
            "type": Variant("s", kind),
            **extra,
        }
        await call(
            fake,
            RX_PATH,
            CHARACTERISTIC,
            "WriteValue",
            "aya{sv}",
            [data, options],
            destination=name,
        )

    try:
        # BlueZ subscribes for the central, the central opens the link, the radio accepts
        await call(fake, TX_PATH, CHARACTERISTIC, "StartNotify", destination=name)
        tag = new_tag()
        await write(encode(Open(tag, central_beacon, 182, Limits().max_message)))
        await until(lambda: notified)
        assert decode(notified[0]) == Accept(tag, beacon, 182, Limits().max_message)
        # a request, written as the central writes it, and its answer notified back
        body = b"hello over d-bus " * 30
        datagrams = split(
            body,
            tag=tag,
            msg_id=1,
            role=Role.REQUEST,
            status=0,
            media=Media.JOSE,
            datagram_size=182,
        )
        for i, datagram in enumerate(datagrams):
            await write(datagram, "request" if i == len(datagrams) - 1 else "command")
        reassembler = Reassembler(Limits(), peer_bit=PERIPHERAL_BIT, awaiting={1}.__contains__)
        answers: list[LinkMessage] = []

        def answered() -> bool:
            while len(notified) > 1:
                frame = decode(notified.pop(1))
                if isinstance(frame, Data):
                    got = reassembler.feed(frame, time.monotonic())
                    if isinstance(got, LinkMessage):
                        answers.append(got)
            return bool(answers)

        await until(answered)
        assert (answers[0].status, answers[0].body) == (200, b"echo:" + body)
        # writes a passport link never makes come back as BlueZ's own errors
        with pytest.raises(BlueZError) as too_long:
            await write(bytes(183))
        assert too_long.value.name == INVALID_LENGTH
        with pytest.raises(BlueZError) as offset:
            await write(b"x", offset=Variant("q", 3))
        assert offset.value.name == NOT_SUPPORTED
        # the central disconnects: BlueZ says so, and the link it held closes
        [link] = table.links()
        fake.send(
            Message.new_signal(
                DEVICE,
                f"{DBUS}.Properties",
                "PropertiesChanged",
                "sa{sv}as",
                ["org.bluez.Device1", {"Connected": Variant("b", False)}, []],
            )
        )
        await until(lambda: link.closed is not None)
        assert "disconnected" in (link.closed or "")
        # a new advert is registered again, no sooner than the radio allows
        monkeypatch.setattr(bluez_radio, "ADVERT_EVERY_S", 0.1)
        before = registry.calls.count(("RegisterAdvertisement", ADVERT_PATH))
        await radio.set_advert(ble_service_data(new_beacon_id(), lan=True, link=True))
        await until(lambda: registry.calls.count(("RegisterAdvertisement", ADVERT_PATH)) > before)
        # bluetoothd goes away: the radio says so, and does not pretend to advertise
        await fake.release_name("org.bluez")
        await until(lambda: radio.status().state == "missing")
        assert "bluetoothd" in radio.status().detail
    finally:
        await table.close_all()
        table.shutdown()
        await radio.stop()


async def test_with_nothing_owning_org_bluez_the_radio_starts_missing_and_says_why() -> None:
    radio = BlueZRadio(bus="session", scan=False)
    status = await radio.start(
        advert=bytes(10), on_sighting=lambda s: None, on_link=lambda p, a: None
    )
    try:
        assert status.state == "missing" and "bluetoothd" in status.detail
        assert status.hint and "systemctl" in status.hint
        assert not (status.advertising or status.accepting or status.scanning)
    finally:
        await radio.stop()
