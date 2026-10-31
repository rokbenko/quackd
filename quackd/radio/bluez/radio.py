"""`BlueZRadio`: a robot's Bluetooth as a `quackd.radio.Radio`, both roles on one adapter.

It advertises and serves the passport's GATT service as a peripheral (`peripheral.py`), and
scans and dials as a central (`central.py`), all on the radio's one event loop. Starting it never
raises for want of Bluetooth: no bus, no bluetoothd, no adapter, an adapter powered off or a bus
that refuses this user each come back as a status that says so and what would change it, and a
watchdog tries again, every 15 seconds at first and every minute after a while, and at once
when bluetoothd comes back on the bus. Whatever part of it does come up runs: an adapter that
will not advertise still scans, and the status says `degraded` and which part failed.

BlueZ pauses discovery to connect on many controllers, so dialling a robot stops the scanner for
the length of the connect and starts it again after, and `on_scanning` tells the service so it
can stop proximity's clock meanwhile. A central that disconnects, or bluetoothd going away, is
heard as D-Bus signals and closes the pipes they carried.

Written against BlueZ's documented API, dbus-fast 5.2 and bleak 3.0.2, and tested against a fake
BlueZ on a private bus; not yet run on a robot's own controller.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable
from typing import Any, Literal

from dbus_fast import Message, MessageType
from dbus_fast.aio import MessageBus

from quackd.radio import (
    ADVERT_EVERY_S,
    Pipe,
    RadioState,
    RadioStatus,
    RadioUnavailable,
    Sighting,
)
from quackd.radio.bluez.central import Scanner, open_pipe
from quackd.radio.bluez.core import (
    ADAPTER_IFACE,
    BLUEZ,
    DEVICE_IFACE,
    ClientPipe,
    PeripheralCore,
    ServerPipe,
)
from quackd.radio.bluez.objects import plain
from quackd.radio.bluez.peripheral import BlueZError, Peripheral, call
from quackd.radio.bluez.probe import DBUS, Probe, connect_bus, probe

#: How long the watchdog waits between tries while the radio is not ready, longer each time.
RETRY_S = (15.0, 15.0, 30.0, 30.0, 60.0)


class BlueZRadio:
    """The radio on one BlueZ adapter (`hci0`), on the system bus or a bus a test names."""

    kind = "bluez"

    def __init__(
        self,
        *,
        adapter: str = "hci0",
        bus: Literal["system", "session"] = "system",
        bus_address: str | None = None,
        advertise: bool = True,
        accept: bool = True,
        scan: bool = True,
        on_scanning: Callable[[bool], None] | None = None,
    ) -> None:
        self.adapter = adapter
        self.bus_kind, self.bus_address = bus, bus_address
        self.advertise, self.accept, self.scan = advertise, accept, scan
        self.on_scanning = on_scanning
        self._bus: MessageBus | None = None
        self._peripheral: Peripheral | None = None
        self._core: PeripheralCore | None = None
        self._scanner: Scanner | None = None
        self._probe: Probe | None = None
        self._advert = b""
        self._pending: bytes | None = None
        self._advert_at = -1e9
        self._advert_task: asyncio.Task[None] | None = None
        self._watchdog: asyncio.Task[None] | None = None
        self._on_sighting: Callable[[Sighting], None] | None = None
        self._on_link: Callable[[Pipe, str], None] | None = None
        self._pipes: set[ClientPipe] = set()
        self._problems: list[str] = []
        #: dials under way: the first pauses the scanner and the last starts it again
        self._connecting = 0
        self._scan_paused = False
        #: bringing the radio up and taking it down, one at a time, whoever asks: the watchdog,
        #: bluetoothd coming back, or start and stop
        self._lifecycle = asyncio.Lock()
        self._tasks: set[asyncio.Task[None]] = set()
        self._stopped = False
        self._status = RadioStatus("off", "bluez", adapter=adapter, detail="not started")

    # ── Radio ────────────────────────────────────────────────────────────────────────────

    async def start(
        self,
        *,
        advert: bytes,
        on_sighting: Callable[[Sighting], None],
        on_link: Callable[[Pipe, str], None],
    ) -> RadioStatus:
        self._advert, self._advert_at = advert, time.monotonic()
        self._on_sighting, self._on_link = on_sighting, on_link
        self._stopped = False
        async with self._lifecycle:
            await self._bring_up()
        self._watchdog = asyncio.get_running_loop().create_task(self._watch())
        return self.status()

    async def set_advert(self, advert: bytes) -> None:
        """Take `advert`, at most one every `ADVERT_EVERY_S`: a change sooner waits, and the
        newest one waiting is the one that goes out."""
        self._pending = advert
        if self._advert_task is None or self._advert_task.done():
            self._advert_task = asyncio.get_running_loop().create_task(self._readvertise())

    async def _readvertise(self) -> None:
        # one task takes every advert handed in while it runs, the newest last: one handed in
        # while BlueZ was registering the one before is not left waiting for nobody
        while self._pending is not None:
            wait = self._advert_at + ADVERT_EVERY_S - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            advert, self._pending = self._pending, None
            if advert is None:
                return
            self._advert, self._advert_at = advert, time.monotonic()
            if self._peripheral is not None and self.advertise:
                try:
                    await self._peripheral.advertise(advert)
                except BlueZError as e:
                    self._problem(f"the new advert was refused: {e}")

    async def connect(self, address: str, *, timeout: float) -> Pipe:  # noqa: ASYNC109
        if self._status.state not in ("ready", "degraded") or self._bus is None:
            raise RadioUnavailable(self._status.state, self._status.detail, self._status.hint)
        scanner = self._scanner
        target: Any = (scanner.devices.get(address) if scanner else None) or address
        self._connecting += 1
        if self._connecting == 1 and scanner is not None and scanner.running:
            self._scan_paused = True
            await scanner.stop()
            self._scanning(False)
        try:
            pipe = await open_pipe(target, adapter=self.adapter, timeout=timeout)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # bleak's errors, D-Bus's and the connect's own timeout
            raise ConnectionError(f"could not connect to {address}: {e}") from e
        finally:
            self._connecting -= 1
            if self._connecting == 0 and self._scan_paused:
                self._scan_paused = False
                if self._scanner is not None and not self._stopped:
                    with contextlib.suppress(Exception):
                        await self._scanner.start()
                    self._scanning(True)
        self._pipes.add(pipe)
        pipe.on_close(lambda _reason: self._pipes.discard(pipe))
        return pipe

    async def stop(self) -> None:
        self._stopped = True
        for task in (self._watchdog, self._advert_task):
            if task is not None:
                task.cancel()
        async with self._lifecycle:
            await self._take_down("the radio stopped")
        if self._bus is not None:
            self._bus.disconnect()
            self._bus = None
        self._status = RadioStatus("off", "bluez", adapter=self.adapter, detail="stopped")

    def status(self) -> RadioStatus:
        facts = self._probe.adapter if self._probe is not None else None
        peripheral = self._peripheral
        scanner = self._scanner
        core = self._core
        return RadioStatus(
            self._status.state,
            "bluez",
            adapter=self.adapter,
            address=facts.address if facts else None,
            address_type=facts.address_type if facts else None,
            advertising=bool(peripheral and peripheral.advertising),
            scanning=bool(scanner and scanner.running),
            accepting=bool(peripheral and peripheral.serving),
            links=len(self._pipes) + (len(core.pipes) if core else 0),
            detail=self._status.detail,
            hint=self._status.hint,
        )

    @property
    def heard(self) -> int:
        """Passport adverts heard since the radio started, for a status's scanning rate."""
        return self._scanner.heard if self._scanner is not None else 0

    # ── bringing it up and down ──────────────────────────────────────────────────────────

    def _set(self, state: RadioState, detail: str, hint: str | None = None) -> None:
        self._status = RadioStatus(state, "bluez", adapter=self.adapter, detail=detail, hint=hint)

    def _problem(self, what: str) -> None:
        self._problems.append(what)
        if self._status.state == "ready":
            self._set("degraded", "; ".join(self._problems))

    async def _bring_up(self) -> None:
        self._problems = []
        if self._bus is None:
            try:
                self._bus = await connect_bus(self.bus_kind, self.bus_address)
            except RadioUnavailable as e:
                self._set(e.state, e.detail, e.hint)
                return
            await self._listen(self._bus)
        found = self._probe = await probe(self._bus, self.adapter)
        if found.state not in ("ready", "degraded"):
            self._set(found.state, found.detail, found.hint)
            return
        core = self._core = PeripheralCore(
            notify=self._notify, on_pipe=self._piped, disconnect=self._disconnect
        )
        peripheral = self._peripheral = Peripheral(self._bus, self.adapter, core)
        denied = False
        if self.accept:
            try:
                await peripheral.serve()
            except BlueZError as e:
                denied |= e.denied
                self._problems.append(f"BlueZ would not take the GATT service: {e}")
        if self.advertise:
            try:
                await peripheral.advertise(self._advert)
            except BlueZError as e:
                denied |= e.denied
                self._problems.append(f"BlueZ would not take the advert: {e}")
        if self.scan:
            try:
                scanner = self._scanner = Scanner(adapter=self.adapter, on_sighting=self._sighted)
                await scanner.start()
                self._scanning(True)
            except Exception as e:  # bleak raises its own errors, and D-Bus's under them
                self._problems.append(f"scanning would not start: {e}")
        if not self._problems:
            self._set("ready", found.detail)
        elif denied and not (peripheral.serving or peripheral.advertising):
            self._set("denied", "; ".join(self._problems), "sudo usermod -aG bluetooth $USER")
        else:
            self._set("degraded", "; ".join(self._problems), found.hint)

    async def _take_down(self, reason: str) -> None:
        for pipe in list(self._pipes):
            await pipe.close()
        if self._scanner is not None:
            with contextlib.suppress(Exception):
                await self._scanner.stop()
            self._scanner = None
            self._scanning(False)
        if self._peripheral is not None:
            with contextlib.suppress(Exception):
                await self._peripheral.close()
            self._peripheral = None
        if self._core is not None:
            self._core.close_all(reason)
            self._core = None

    async def _watch(self) -> None:
        """Try again while the radio is not ready: bluetoothd started late, an adapter was
        powered on, a user was added to the bluetooth group and the service restarted."""
        tries = 0
        while not self._stopped:
            if self._status.state in ("ready", "degraded"):
                tries = 0
                await asyncio.sleep(RETRY_S[0])
                continue
            await asyncio.sleep(RETRY_S[min(tries, len(RETRY_S) - 1)])
            tries += 1
            async with self._lifecycle:
                if self._status.state not in ("ready", "degraded"):
                    await self._take_down("the radio is starting again")
                    await self._bring_up()

    # ── what BlueZ says ──────────────────────────────────────────────────────────────────

    async def _listen(self, bus: MessageBus) -> None:
        """Signals: a central gone, the adapter powered off, bluetoothd gone or back."""
        rules = [
            f"type='signal',sender='{BLUEZ}',interface='org.freedesktop.DBus.Properties',"
            "member='PropertiesChanged'",
            f"type='signal',sender='{BLUEZ}',interface='org.freedesktop.DBus.ObjectManager',"
            "member='InterfacesRemoved'",
            f"type='signal',sender='{DBUS}',interface='{DBUS}',member='NameOwnerChanged',"
            f"arg0='{BLUEZ}'",
        ]
        for rule in rules:
            with contextlib.suppress(BlueZError):
                await call(
                    bus, "/org/freedesktop/DBus", DBUS, "AddMatch", "s", [rule], destination=DBUS
                )
        bus.add_message_handler(self._signal)

    def _signal(self, msg: Message) -> bool:
        if msg.message_type != MessageType.SIGNAL:
            return False
        if msg.member == "PropertiesChanged" and msg.body and len(msg.body) >= 2:
            interface, changed = msg.body[0], plain(msg.body[1])
            if interface == DEVICE_IFACE and changed.get("Connected") is False:
                self._device_gone(str(msg.path))
            elif (
                interface == ADAPTER_IFACE
                and msg.path == f"/org/bluez/{self.adapter}"
                and changed.get("Powered") is False
            ):
                self._lost("off", f"{self.adapter} was powered off", "bluetoothctl power on")
        elif msg.member == "InterfacesRemoved" and msg.body and len(msg.body) >= 2:
            path, interfaces = str(msg.body[0]), list(msg.body[1])
            if DEVICE_IFACE in interfaces:
                self._device_gone(path)
            if ADAPTER_IFACE in interfaces and path == f"/org/bluez/{self.adapter}":
                self._lost("missing", f"{self.adapter} went away", None)
        elif msg.member == "NameOwnerChanged" and msg.body and msg.body[0] == BLUEZ:
            if not msg.body[2]:
                self._lost("missing", "bluetoothd stopped", "sudo systemctl start bluetooth")
            elif not self._stopped:
                # bluetoothd is back, and remembers nothing this robot registered with it
                self._spawn(self._restart())
        return False

    def _dialled(self, device: str) -> list[ClientPipe]:
        """The links this robot dialled to the device at `device`, by its address, and never
        by an empty one."""
        found = []
        for pipe in self._pipes:
            address = str(getattr(pipe.client, "address", "") or "").upper()
            if address and device.endswith("/dev_" + address.replace(":", "_")):
                found.append(pipe)
        return found

    def _device_gone(self, device: str) -> None:
        if self._core is not None:
            self._core.device_gone(device)
        # bleak's own disconnect callback closes a link this robot dialled too; this catches the
        # one it might miss
        for pipe in self._dialled(device):
            pipe.lost()

    def _lost(self, state: RadioState, detail: str, hint: str | None) -> None:
        self._set(state, detail, hint)
        for pipe in list(self._pipes):
            pipe.lost(f"link lost: {detail}")
        if self._core is not None:
            self._core.close_all(f"link lost: {detail}")

    async def _restart(self) -> None:
        async with self._lifecycle:
            if self._stopped:
                return
            await self._take_down("bluetoothd restarted")
            await self._bring_up()

    def _spawn(self, coro: Any) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ── the peripheral's callbacks ───────────────────────────────────────────────────────

    def _notify(self, datagram: bytes) -> None:
        if self._peripheral is not None:
            self._peripheral.notify(datagram)

    def _piped(self, pipe: ServerPipe, address: str) -> None:
        if self._on_link is not None:
            self._on_link(pipe, address)

    def _disconnect(self, device: str) -> None:
        # one LE connection joins two devices, and a link this robot dialled to the same robot
        # may ride it: the connection stays while that link does
        if self._peripheral is not None and not self._dialled(device):
            self._spawn(self._peripheral.disconnect(device))

    def _sighted(self, sighting: Sighting) -> None:
        if self._on_sighting is not None:
            self._on_sighting(sighting)

    def _scanning(self, on: bool) -> None:
        if self.on_scanning is not None:
            self.on_scanning(on)


__all__ = ["BlueZRadio"]
