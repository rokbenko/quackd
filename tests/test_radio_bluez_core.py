"""The BlueZ radio's logic with no bus: writes from centrals, pipes, notifications, a client pipe
over a stand-in for bleak's client, sightings from what bleak reports, and what BlueZ's object
tree says about an adapter. Runs on every platform; the D-Bus half has its own tests."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from quackd.passport.beacon import (
    BLE_SERVICE_UUID,
    FLAG_LAN,
    FLAG_LINK,
    LEGACY_AD_BYTES,
    ble_service_data,
    new_beacon_id,
)
from quackd.passport.link import MAX_DATAGRAM, MIN_DATAGRAM, Accept, Open, decode, encode, new_tag
from quackd.radio import PipeClosed
from quackd.radio.bluez.core import (
    DEFAULT_MTU,
    FAILED,
    INVALID_LENGTH,
    MAX_CENTRALS,
    NOT_PERMITTED,
    NOT_SUPPORTED,
    AdapterFacts,
    ClientPipe,
    PeripheralCore,
    ServerPipe,
    WriteRefused,
    adapter_state,
    adapters_from,
    address_of,
    advert_bytes,
    advert_properties,
    device_path,
    rfkill_blocked,
    settled_write_size,
    sighting_from,
)
from quackd.radio.links import Link

DEVICE = "/org/bluez/hci0/dev_11_22_33_44_55_66"


class Central:
    """What the GATT server's callbacks reached: pipes made, notifications sent, disconnects."""

    def __init__(self) -> None:
        self.pipes: list[tuple[ServerPipe, str]] = []
        self.notified: list[bytes] = []
        self.disconnected: list[str] = []
        self.core = PeripheralCore(
            notify=self.notified.append,
            on_pipe=lambda pipe, address: self.pipes.append((pipe, address)),
            disconnect=self.disconnected.append,
            pace_s=0.0,
        )


def write(core: PeripheralCore, value: bytes, device: str = DEVICE, **options: Any) -> None:
    core.on_write(value, {"device": device, "type": "request", **options})


def test_device_paths_and_addresses_go_both_ways() -> None:
    assert address_of(DEVICE) == "11:22:33:44:55:66"
    assert device_path("hci0", "11:22:33:44:55:66") == DEVICE
    assert device_path("hci1", "aa:bb:cc:dd:ee:ff") == "/org/bluez/hci1/dev_AA_BB_CC_DD_EE_FF"
    assert address_of("/org/bluez/hci0") is None and address_of("/elsewhere/dev_11") is None
    with pytest.raises(ValueError):
        device_path("hci0", "not an address")


def test_the_advert_asked_of_bluez_is_the_31_bytes_of_a_legacy_advert() -> None:
    data = ble_service_data(new_beacon_id(), lan=True, link=True)
    props = advert_properties(data)
    assert props["Type"] == "peripheral" and props["Includes"] == []
    assert "LocalName" not in props and "TxPower" not in props
    assert len(advert_bytes(props)) == LEGACY_AD_BYTES
    with pytest.raises(ValueError):
        advert_bytes({**props, "LocalName": "duck"})
    with pytest.raises(ValueError):
        advert_bytes({**props, "Includes": ["tx-power"]})


def test_a_central_first_write_makes_its_pipe_named_by_its_address() -> None:
    c = Central()
    write(c.core, b"hello", mtu=185)
    [(pipe, address)] = c.pipes
    assert address == "11:22:33:44:55:66" and pipe.datagram_size == 185 - 3
    got: list[bytes] = []
    pipe.on_datagram(got.append)  # what arrived before the callback was kept for it
    write(c.core, b"again", mtu=185)
    assert got == [b"hello", b"again"]
    assert len(c.pipes) == 1  # the same central, the same pipe


def test_a_write_is_sized_by_the_mtu_bluez_reports_and_never_past_the_attribute_limit() -> None:
    c = Central()
    write(c.core, bytes(DEFAULT_MTU - 3))  # no mtu option: the default before any exchange
    assert c.pipes[0][0].datagram_size == DEFAULT_MTU - 3
    with pytest.raises(WriteRefused) as refused:
        write(c.core, bytes(DEFAULT_MTU - 2), device=DEVICE.replace("66", "77"))
    assert refused.value.error == INVALID_LENGTH
    write(c.core, b"x", device=DEVICE.replace("66", "88"), mtu=4000)
    assert c.pipes[-1][0].datagram_size == MAX_DATAGRAM
    write(c.core, b"x", device=DEVICE.replace("66", "99"), mtu=10)
    assert c.pipes[-1][0].datagram_size == MIN_DATAGRAM


@pytest.mark.parametrize(
    ("options", "error"),
    [
        ({"offset": 4}, NOT_SUPPORTED),
        ({"type": "reliable"}, NOT_SUPPORTED),
        ({"prepare-authorize": True}, NOT_SUPPORTED),
        ({"device": ""}, FAILED),
    ],
)
def test_writes_a_passport_link_never_makes_are_refused(
    options: dict[str, Any], error: str
) -> None:
    c = Central()
    with pytest.raises(WriteRefused) as refused:
        write(c.core, b"x", **options)
    assert refused.value.error == error
    assert c.pipes == []


def test_past_its_centrals_the_server_refuses_a_new_one_and_takes_it_once_one_leaves() -> None:
    c = Central()
    devices = [f"/org/bluez/hci0/dev_11_22_33_44_55_{i:02X}" for i in range(MAX_CENTRALS + 1)]
    for device in devices[:-1]:
        write(c.core, b"x", device=device)
    with pytest.raises(WriteRefused) as refused:
        write(c.core, b"x", device=devices[-1])
    assert refused.value.error == NOT_PERMITTED
    c.core.device_gone(devices[0])
    write(c.core, b"x", device=devices[-1])
    assert len(c.core.pipes) == MAX_CENTRALS


async def test_notifications_go_out_only_to_subscribers_and_closing_asks_bluez_to_disconnect() -> (
    None
):
    c = Central()
    write(c.core, b"open")
    pipe = c.pipes[0][0]
    await pipe.send(b"unheard")
    assert c.notified == [] and pipe.unheard == 1
    c.core.start_notify()
    await pipe.send(b"heard")
    assert c.notified == [b"heard"]
    with pytest.raises(ValueError):
        await pipe.send(bytes(pipe.datagram_size + 1))
    reasons: list[str] = []
    pipe.on_close(reasons.append)
    await pipe.close()
    assert reasons == ["closed by this end"] and c.disconnected == [DEVICE]
    with pytest.raises(PipeClosed):
        await pipe.send(b"after")
    # a central that went away closes its pipe without anyone asking BlueZ for anything
    write(c.core, b"open again")
    again = c.pipes[-1][0]
    c.core.device_gone(DEVICE)
    assert again.closed and again.closed.startswith("link lost")
    assert c.disconnected == [DEVICE]


async def test_a_link_opens_over_the_gatt_server_as_over_any_pipe() -> None:
    c = Central()
    c.core.start_notify()
    me, them = new_beacon_id(), new_beacon_id()
    accepted: list[Link | None] = []

    def on_pipe(pipe: ServerPipe, address: str) -> None:
        async def take() -> None:
            accepted.append(await Link.accept(pipe, me=me, inbox=lambda b, m: (200, b, m)))

        asyncio.get_running_loop().create_task(take())

    c.core.on_pipe = on_pipe
    tag = new_tag()
    write(c.core, encode(Open(tag, them, 182, 16_384)), mtu=185)
    for _ in range(50):
        if c.notified:
            break
        await asyncio.sleep(0.01)
    assert decode(c.notified[0]) == Accept(tag, me, 182, 16_384)
    await asyncio.sleep(0)
    assert accepted and accepted[0] is not None and accepted[0].peer_beacon == them


class StandInClient:
    """The part of bleak's client a client pipe uses, keeping what it was asked."""

    def __init__(self, fail: bool = False) -> None:
        self.writes: list[tuple[bytes, bool | None]] = []
        self.stopped: list[Any] = []
        self.disconnects = 0
        self.fail = fail

    async def write_gatt_char(
        self, char_specifier: Any, data: Any, response: bool | None = None
    ) -> None:
        if self.fail:
            raise OSError("org.bluez.Error.Failed: Not connected")
        self.writes.append((bytes(data), response))

    async def stop_notify(self, char_specifier: Any) -> None:
        self.stopped.append(char_specifier)

    async def disconnect(self) -> None:
        self.disconnects += 1


async def test_a_client_pipe_writes_barriers_as_requests_and_the_rest_as_commands() -> None:
    client = StandInClient()
    pipe = ClientPipe(client, "rx", "tx", 182)
    await pipe.send(b"one", barrier=False)
    await pipe.send(b"two")
    assert client.writes == [(b"one", False), (b"two", True)]
    got: list[bytes] = []
    pipe.on_datagram(got.append)
    pipe.received(bytearray(b"notified"))
    assert got == [b"notified"]
    await pipe.close()
    await pipe.close()
    assert client.stopped == ["tx"] and client.disconnects == 1
    with pytest.raises(PipeClosed):
        await pipe.send(b"three")


async def test_a_write_that_fails_or_a_disconnect_closes_the_client_pipe() -> None:
    failing = ClientPipe(StandInClient(fail=True), "rx", "tx", 182)
    with pytest.raises(PipeClosed, match="link lost"):
        await failing.send(b"x")
    assert failing.closed and failing.closed.startswith("link lost")
    dropped = ClientPipe(StandInClient(), "rx", "tx", 182)
    reasons: list[str] = []
    dropped.on_close(reasons.append)
    dropped.lost()
    assert reasons == ["link lost: disconnected"]


async def test_the_write_size_settles_once_it_leaves_the_default_and_never_past_512() -> None:
    sizes = iter([MIN_DATAGRAM, MIN_DATAGRAM, 182])
    assert await settled_write_size(lambda: next(sizes), within_s=2.0) == 182
    assert await settled_write_size(lambda: MIN_DATAGRAM, within_s=0.2) == MIN_DATAGRAM
    assert await settled_write_size(lambda: 4096, within_s=0.2) == MAX_DATAGRAM


def test_a_sighting_is_made_only_from_a_passport_advert_with_a_real_strength() -> None:
    beacon = new_beacon_id()
    data = {BLE_SERVICE_UUID: ble_service_data(beacon, lan=True, link=True)}
    seen = sighting_from("AA:BB:CC:DD:EE:FF", -61, data, 5.0)
    assert seen is not None
    assert (seen.beacon_id, seen.flags, seen.rssi, seen.address, seen.at) == (
        beacon,
        FLAG_LAN | FLAG_LINK,
        -61.0,
        "AA:BB:CC:DD:EE:FF",
        5.0,
    )
    assert sighting_from("AA:BB:CC:DD:EE:FF", 127, data, 5.0) is None  # HCI's "no RSSI"
    assert sighting_from("AA:BB:CC:DD:EE:FF", None, data, 5.0) is None
    other = {"0000feaa-0000-1000-8000-00805f9b34fb": data[BLE_SERVICE_UUID]}
    assert sighting_from("AA:BB:CC:DD:EE:FF", -61, other, 5.0) is None
    assert sighting_from("AA:BB:CC:DD:EE:FF", -61, {BLE_SERVICE_UUID: b"\x02"}, 5.0) is None


class V:
    """A variant as dbus-fast hands one over: a value and its signature."""

    def __init__(self, signature: str, value: Any) -> None:
        self.signature, self.value = signature, value


def tree(**adapter: Any) -> dict[str, dict[str, dict[str, Any]]]:
    props = {
        "Address": V("s", "AA:BB:CC:DD:EE:FF"),
        "AddressType": V("s", "public"),
        "Alias": V("s", "robot"),
        "Powered": V("b", True),
        "Roles": V("as", ["central", "peripheral"]),
    }
    props.update({k: V("?", v) for k, v in adapter.items()})
    return {
        "/org/bluez": {"org.bluez.AgentManager1": {}},
        "/org/bluez/hci0": {
            "org.bluez.Adapter1": props,
            "org.bluez.GattManager1": {},
            "org.bluez.LEAdvertisingManager1": {"SupportedInstances": V("y", 5)},
        },
        DEVICE: {"org.bluez.Device1": {"Connected": V("b", False)}},
    }


def test_an_adapter_is_read_off_bluez_object_tree() -> None:
    [facts] = adapters_from(tree())
    assert facts == AdapterFacts(
        path="/org/bluez/hci0",
        address="AA:BB:CC:DD:EE:FF",
        address_type="public",
        alias="robot",
        powered=True,
        roles=("central", "peripheral"),
        advertising=True,
        supported_instances=5,
        gatt=True,
    )
    assert facts.name == "hci0" and facts.peripheral is True
    assert adapter_state(facts, "hci0")[0] == "ready"


@pytest.mark.parametrize(
    ("changes", "state", "hint"),
    [
        ({"Powered": False}, "off", "bluetoothctl power on"),
        ({"Roles": ["central"]}, "unsupported", None),
    ],
)
def test_what_an_adapter_lacks_is_said_plainly(
    changes: dict[str, Any], state: str, hint: str | None
) -> None:
    [facts] = adapters_from(tree(**changes))
    got = adapter_state(facts, "hci0")
    assert got[0] == state and got[2] == hint and got[1]


def test_no_adapter_or_a_missing_manager_is_missing_or_degraded() -> None:
    assert adapter_state(None, "hci1")[0] == "missing"
    objects = tree()
    del objects["/org/bluez/hci0"]["org.bluez.LEAdvertisingManager1"]
    [facts] = adapters_from(objects)
    state, detail, _ = adapter_state(facts, "hci0")
    assert state == "degraded" and "advertising" in detail
    # an older BlueZ that lists no roles is not refused for it
    del objects["/org/bluez/hci0"]["org.bluez.Adapter1"]["Roles"]
    [facts] = adapters_from(objects)
    assert facts.roles is None and facts.peripheral is None


def test_rfkill_is_read_for_bluetooth_switches_only() -> None:
    assert rfkill_blocked([]) is None
    assert rfkill_blocked([{"type": "wlan", "soft": "1", "hard": "0"}]) is None
    assert rfkill_blocked([{"type": "bluetooth", "soft": "0", "hard": "0"}]) is False
    assert rfkill_blocked([{"type": "bluetooth", "soft": "1\n", "hard": "0"}]) is True
    assert rfkill_blocked([{"type": "bluetooth", "soft": "0", "hard": "1"}]) is True


def test_a_server_pipe_carries_a_whole_hello_in_order() -> None:
    async def go() -> list[bytes]:
        c = Central()
        c.core.start_notify()
        write(c.core, b"open", mtu=185)
        pipe = c.pipes[0][0]
        for i in range(20):
            await pipe.send(bytes([i]) * 10, barrier=i == 19)
        return c.notified

    notified = asyncio.run(go())
    assert notified == [bytes([i]) * 10 for i in range(20)]
