"""Stand-ins for the air, for the tests: radios, pipes and a LAN, in one process and one loop.

`MemoryPipe.pair` is a link's two ends with nothing between them but an optional delay, so a
link, a reassembler and everything above them run in a test exactly as they would over a radio,
in milliseconds. `overheard_by` is BlueZ's one surprise for a link: a peripheral's notification
reaches every central subscribed to its characteristic, not only the one it was meant for.

`FakeAir` holds radios at places a test sets and moves, and each `step` delivers every advert
once, at the strength the sim's model gives the distance, on a clock the test advances; `FakeLan`
is a LAN in a dict. Together they run proximity, the meeting planner and links end to end with
no files, no sockets and no waiting on real time.
"""

from __future__ import annotations

import asyncio
import math
import random
from collections.abc import Callable

from quackd.lan.lookup import LanRecord
from quackd.passport.beacon import read_ble_service_data
from quackd.radio import Pipe, PipeClosed, RadioStatus, Sighting
from quackd.radio.pipe import BasePipe
from quackd.radio.sim import AirModel

FAKE_PREFIX = "fake:"

_STOP = object()


class MemoryPipe(BasePipe):
    """One end of a pair of queues. Datagrams arrive in order, each after `latency` seconds;
    a send with `barrier=True` returns once the other end has taken the datagram."""

    def __init__(self, datagram_size: int, *, latency: float = 0.0) -> None:
        super().__init__(datagram_size)
        self.latency = latency
        self.peer: MemoryPipe | None = None
        #: other ends that hear what this end sends too, as every central subscribed to a
        #: peripheral hears each notification it sends
        self.overheard_by: list[MemoryPipe] = []
        #: datagrams this end sent, for a test to count
        self.sent = 0
        self._queue: asyncio.Queue[object] | None = None

    @classmethod
    def pair(
        cls, size: int = 182, *, latency: float = 0.0, other_size: int | None = None
    ) -> tuple[MemoryPipe, MemoryPipe]:
        """Two ends joined to each other: the first sends datagrams of up to `size` bytes, the
        second of up to `other_size` (`size` unless given)."""
        a = cls(size, latency=latency)
        b = cls(other_size if other_size is not None else size, latency=latency)
        a.peer, b.peer = b, a
        return a, b

    def _incoming(self) -> asyncio.Queue[object]:
        if self._queue is None:
            self._queue = asyncio.Queue()
            asyncio.get_running_loop().create_task(self._pump(self._queue))
        return self._queue

    async def _pump(self, queue: asyncio.Queue[object]) -> None:
        while (item := await queue.get()) is not _STOP:
            assert isinstance(item, tuple)
            datagram, done = item
            if self.latency:
                await asyncio.sleep(self.latency)
            if self.closed is None:
                self._received(datagram)
                if done is not None and not done.done():
                    done.set_result(None)
            elif done is not None and not done.done():
                done.set_exception(PipeClosed(self.closed))

    async def send(self, datagram: bytes, *, barrier: bool = True) -> None:
        peer = self.peer
        if self.closed is not None or peer is None or peer.closed is not None:
            raise PipeClosed(self.closed or "the other end is gone")
        if len(datagram) > self.datagram_size:
            raise ValueError(f"{len(datagram)} bytes, over this pipe's {self.datagram_size}")
        self.sent += 1
        done = asyncio.get_running_loop().create_future() if barrier else None
        peer._incoming().put_nowait((datagram, done))
        for other in self.overheard_by:
            if other.closed is None:
                other._incoming().put_nowait((datagram, None))
        if done is not None:
            await done

    def _close(self, reason: str) -> None:
        if self._ended(reason) and self._queue is not None:
            self._queue.put_nowait(_STOP)  # the pump fails what is left behind it, then ends

    async def close(self) -> None:
        self._close("closed by this end")
        if self.peer is not None:
            self.peer._close("closed by the other end")

    def cut(self, reason: str = "link lost") -> None:
        """Both ends lose the link at once, the way a radio drops one out of range."""
        self._close(reason)
        if self.peer is not None:
            self.peer._close(reason)


class FakeRadio:
    """A `quackd.radio.Radio` on a `FakeAir`: it advertises what it was handed and hears what
    the air delivers when the test steps it; dialling another fake radio in range hands that one
    the other end of a `MemoryPipe`."""

    kind = "fake"

    def __init__(self, air: FakeAir, name: str, at: tuple[float, float], datagram_size: int):
        self.air, self.name, self.at, self.datagram_size = air, name, at, datagram_size
        self.address = FAKE_PREFIX + name
        self.advert: bytes | None = None
        self.on_sighting: Callable[[Sighting], None] | None = None
        self.on_link: Callable[[Pipe, str], None] | None = None
        self.state: str = "off"
        self.pipes: list[tuple[MemoryPipe, str]] = []

    async def start(
        self,
        *,
        advert: bytes,
        on_sighting: Callable[[Sighting], None],
        on_link: Callable[[Pipe, str], None],
    ) -> RadioStatus:
        self.advert, self.on_sighting, self.on_link = advert, on_sighting, on_link
        self.state = "ready"
        return self.status()

    async def set_advert(self, advert: bytes) -> None:
        self.advert = advert

    async def connect(self, address: str, *, timeout: float) -> Pipe:  # noqa: ASYNC109
        other = self.air.radios.get(address.removeprefix(FAKE_PREFIX))
        if self.state != "ready" or other is None or other.state != "ready":
            raise ConnectionError(f"nothing is advertising at {address}")
        if not self.air.model.in_range(self.air.distance(self.name, other.name)):
            raise ConnectionError(f"{address} is out of radio range")
        if other.on_link is None:
            raise ConnectionError(f"{address} takes no links")
        mine, theirs = MemoryPipe.pair(min(self.datagram_size, other.datagram_size))
        self.pipes.append((mine, other.name))
        other.pipes.append((theirs, self.name))
        other.on_link(theirs, self.address)
        return mine

    async def stop(self) -> None:
        for pipe, _other in self.pipes:
            pipe.cut("the radio stopped")
        self.pipes.clear()
        self.state = "off"

    def status(self) -> RadioStatus:
        ready = self.state == "ready"
        return RadioStatus(
            "ready" if ready else "off",
            "fake",
            adapter="fake",
            address=self.address,
            advertising=ready,
            scanning=ready,
            accepting=ready,
            links=sum(1 for pipe, _ in self.pipes if pipe.closed is None),
            detail="a fake radio, in a test",
        )


class FakeAir:
    """Fake radios at places in metres, with the sim's model for how loud each hears another;
    `clock` is the air's time, which only `step` moves."""

    def __init__(self, model: AirModel | None = None, *, seed: int = 0) -> None:
        self.model = model or AirModel()
        self.rng = random.Random(seed)
        self.clock = 0.0
        self.radios: dict[str, FakeRadio] = {}

    def radio(
        self, name: str, at: tuple[float, float] = (0.0, 0.0), *, datagram_size: int = 182
    ) -> FakeRadio:
        radio = self.radios[name] = FakeRadio(self, name, at, datagram_size)
        return radio

    def move(self, name: str, at: tuple[float, float]) -> None:
        self.radios[name].at = at

    def distance(self, a: str, b: str) -> float:
        (xa, ya), (xb, yb) = self.radios[a].at, self.radios[b].at
        return math.hypot(xa - xb, ya - yb)

    async def step(self, dt: float = 0.2) -> None:
        """Let `dt` seconds of air pass: every radio hears every other's advert once, unless the
        model loses it, and every link whose two radios are out of range drops."""
        self.clock += dt
        live = [r for r in self.radios.values() if r.state == "ready" and r.advert is not None]
        for receiver in live:
            for sender in live:
                if sender is receiver or receiver.on_sighting is None:
                    continue
                metres = self.distance(receiver.name, sender.name)
                rssi = self.model.heard(metres, self.rng)
                beacon = read_ble_service_data(sender.advert or b"")
                if rssi is None or beacon is None:
                    continue
                receiver.on_sighting(
                    Sighting(
                        beacon.beacon_id,
                        beacon.flags,
                        rssi,
                        sender.address,
                        self.clock,
                        true_m=round(metres, 2),
                    )
                )
        for radio in self.radios.values():
            for pipe, other in radio.pipes:
                if pipe.closed is None and not self.model.in_range(
                    self.distance(radio.name, other)
                ):
                    pipe.cut("link lost: out of range")
        await asyncio.sleep(0)

    def cut(self, a: str, b: str) -> None:
        """Drop every link between `a` and `b`, as interference would."""
        for pipe, other in self.radios[a].pipes:
            if other == b:
                pipe.cut()


class FakeLan:
    """A LAN in a dict: the records a test publishes, found by the beacon id each carries."""

    def __init__(self) -> None:
        self.records: dict[str, LanRecord] = {}

    def publish(self, record: LanRecord) -> None:
        self.records[record.short_id] = record

    def withdraw(self, short_id: str) -> None:
        self.records.pop(short_id, None)

    def lookup(self, beacon_id: str) -> LanRecord | None:
        return next((r for r in self.records.values() if r.beacon_id == beacon_id), None)
