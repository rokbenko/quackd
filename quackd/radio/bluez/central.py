"""The robot as a central: bleak's scanner hearing adverts, and bleak's client dialling a link.

The scanner runs BlueZ's active discovery on LE only, and asks BlueZ not to drop repeated adverts
(its DuplicateData filter), since proximity needs a robot's strength several times a second, not
once. That is BlueZ's own filter only: whether the controller under it filters duplicates too,
which bleak says the setting does not change, and which would leave proximity one advert of each
robot, too few for its median, has not been seen on a Jetson. If it does, passive scanning
through an advertisement monitor is the way round it. Adverts are filtered here by the
passport's service data rather than by BlueZ, whose discovery filter matches advertised service
UUIDs and not service data keys.

Dialling a link is bleak's `BleakClient` on the device the scanner last saw at that address, with
only the passport's service resolved: rx and tx found, the write size settled once the MTU
exchange has finished (bleak reports 20 until it has), and tx subscribed before the link's OPEN
is written, or the ACCEPT would be notified to nobody.

Written against bleak 3.0.2; not yet run against a real controller.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

from quackd.passport.beacon import BLE_RX_UUID, BLE_SERVICE_UUID, BLE_TX_UUID
from quackd.radio import Sighting
from quackd.radio.bluez.core import ClientPipe, settled_write_size, sighting_from

#: Devices remembered by address, to dial one without a fresh discovery: as many as the radio's
#: proximity table holds robots.
REMEMBERED = 256


class NotAPassportLink(ConnectionError):
    """A device that advertised the passport's service data and serves no passport service."""


class Scanner:
    """Active LE discovery, every advert reported, passport adverts turned into sightings."""

    def __init__(self, *, adapter: str, on_sighting: Callable[[Sighting], None]) -> None:
        self.adapter = adapter
        self.on_sighting = on_sighting
        self.devices: OrderedDict[str, BLEDevice] = OrderedDict()
        #: passport adverts heard since the radio started, for a status's scanning rate
        self.heard = 0
        self.running = False
        self._scanner = BleakScanner(
            detection_callback=self._detected,
            scanning_mode="active",
            bluez={"adapter": adapter, "filters": {"Transport": "le", "DuplicateData": True}},
        )

    def _detected(self, device: BLEDevice, advert: AdvertisementData) -> None:
        sighting = sighting_from(device.address, advert.rssi, advert.service_data, time.monotonic())
        if sighting is None:
            return
        self.heard += 1
        self.devices[device.address] = device
        self.devices.move_to_end(device.address)
        while len(self.devices) > REMEMBERED:
            self.devices.popitem(last=False)
        self.on_sighting(sighting)

    async def start(self) -> None:
        await self._scanner.start()
        self.running = True

    async def stop(self) -> None:
        if self.running:
            self.running = False
            await self._scanner.stop()


async def open_pipe(target: BLEDevice | str, *, adapter: str, timeout: float) -> ClientPipe:  # noqa: ASYNC109
    """Connect to the robot at `target` and make the link's pipe over its rx and tx."""
    holder: dict[str, ClientPipe] = {}

    def disconnected(_client: Any) -> None:
        pipe = holder.get("pipe")
        if pipe is not None:
            pipe.lost()

    client = BleakClient(
        target,
        disconnected,
        services=[BLE_SERVICE_UUID],
        timeout=timeout,
        bluez={"adapter": adapter},
    )
    await client.connect()
    try:
        service = client.services.get_service(BLE_SERVICE_UUID)
        rx = service.get_characteristic(BLE_RX_UUID) if service is not None else None
        tx = service.get_characteristic(BLE_TX_UUID) if service is not None else None
        if rx is None or tx is None:
            raise NotAPassportLink(f"{target} serves no passport link")
        size = await settled_write_size(lambda: int(rx.max_write_without_response_size))
        pipe = holder["pipe"] = ClientPipe(client, rx, tx, size)
        await client.start_notify(tx, lambda _char, data: pipe.received(data))
    except BaseException:
        await client.disconnect()
        raise
    return pipe


__all__ = ["NotAPassportLink", "Scanner", "open_pipe"]
