"""The sim air: robots on one laptop that hear each other as if over Bluetooth, between processes.

`quackd onboard serve --board sim` runs a robot's whole service on a laptop, and two of them are
the demo, so the radio they share has to work between processes, on Windows, macOS and Linux,
with no Bluetooth and no multicast. The air is a directory. Each radio writes one small file
there five times a second: the same 31 advert bytes BlueZ would put on the air, where its robot
stands, and the loopback port its "GATT server" listens on. Each radio reads everybody else's
file as often, works out how far away they stand, and hears an advert at a strength the
log-distance model gives for that distance, with noise and some adverts lost, as real ones are.

Where a robot stands is scripted (`Path`, metres at seconds), so the demo can walk two robots
together and apart. A link is a loopback TCP connection carrying the same datagrams a GATT link
would, writes one way and notifications the other, in the same `quackd.passport.link` frames, so
everything above the pipe runs unchanged; a link whose two robots stay out of range drops, as a
BLE link does when its supervision timer runs out. The LAN is a directory too (`SimLan`), one
per network name, and `--network none` puts a robot on none, which is how the demo makes two
robots meet over the link instead.

Files are replaced whole, by a temporary file and `os.replace`, and a reader that races a writer
(Windows refuses to replace a file another process has open, and an antivirus scanner holds
them too) simply misses that advert, which the proximity code already expects of a radio. None
of this has anything to do with real radio propagation beyond the textbook model it uses; it
exists so the code above it runs end to end without hardware.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import os
import pathlib
import random
import re
import secrets
import struct
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from quackd.lan.lookup import LanRecord
from quackd.passport.beacon import (
    BleBeacon,
    advertising_data,
    is_beacon_id,
    read_advertising_data,
)
from quackd.passport.link import MAX_DATAGRAM, MAX_LINKS
from quackd.radio import (
    ADVERT_EVERY_S,
    Pipe,
    PipeClosed,
    RadioStatus,
    Sighting,
)
from quackd.radio.pipe import BasePipe

SIM_PREFIX = "sim:"
#: How often a radio rewrites its file and reads everybody else's: five adverts a second, the
#: middle of what a BLE advertiser sends at the 100 to 200 ms interval a passport asks for.
TICK_S = 0.2
#: A radio whose file is older than this has stopped, or its robot's service has.
STALE_S = 2.0
#: The most of an air file a reader reads; one is about 200 bytes.
MAX_AIR_FILE = 4096
#: The ATT MTU a sim link uses unless told otherwise: what a Jetson and a phone often agree on.
DEFAULT_MTU = 185
#: The smallest ATT MTU there is, the default before any exchange.
MIN_MTU = 23
#: Connections the sim's "GATT server" holds at once, past which it hangs up: room for every
#: link a robot may have and as many again still saying who they are.
MAX_CONNECTIONS = 2 * MAX_LINKS
_TOKEN = re.compile(r"^[0-9a-z_-]{1,40}$")
_OP = struct.Struct(">BH")
OP_WRITE_REQ, OP_WRITE_CMD, OP_NOTIFY, OP_SUBSCRIBE, OP_WRITE_ACK = 1, 2, 3, 4, 5


def default_air_dir() -> pathlib.Path:
    return pathlib.Path.home() / ".quackd" / "sim-air"


# ── the model: where robots stand and how loud they are ─────────────────────────────────────


@dataclass(frozen=True)
class AirModel:
    """Log-distance path loss, the textbook model RSSI-based ranging assumes, with Gaussian noise
    cut off at `noise_bound_db` and a share of adverts lost outright. `supervision_s` is how long
    a link survives with its two robots out of range, as a BLE link's supervision timeout."""

    rssi_at_1m: float = -59.0
    path_loss_n: float = 2.2
    noise_db: float = 2.0
    noise_bound_db: float = 6.0
    floor_dbm: float = -95.0
    loss: float = 0.05
    supervision_s: float = 4.0

    def clean_rssi(self, metres: float) -> float:
        """The strength the model gives `metres`, with no noise: 10 cm is as close as it gets."""
        return self.rssi_at_1m - 10 * self.path_loss_n * math.log10(max(metres, 0.1))

    def heard(self, metres: float, rng: random.Random) -> float | None:
        """One advert from `metres` away, as a receiver hears it, or None when it is lost or
        too weak to hear at all."""
        if rng.random() < self.loss:
            return None
        noise = max(-self.noise_bound_db, min(self.noise_bound_db, rng.gauss(0.0, self.noise_db)))
        rssi = self.clean_rssi(metres) + noise
        return None if rssi < self.floor_dbm else round(rssi, 1)

    def in_range(self, metres: float) -> bool:
        return self.clean_rssi(metres) >= self.floor_dbm


@dataclass(frozen=True)
class Path:
    """Where a robot stands over time: points `x,y@t`, metres at seconds after the scene's
    start, walked in straight lines between them, standing still before the first and after the
    last. `Path.parse("6,0@0 1,0@8 1,0@18 6,0@23")` walks in from 6 m, stays 10 s, walks out."""

    points: tuple[tuple[float, float, float], ...]

    @classmethod
    def parse(cls, text: str) -> Path:
        points: list[tuple[float, float, float]] = []
        for part in text.split():
            where, _at, when = part.partition("@")
            try:
                x_text, y_text = where.split(",")
                point = (float(x_text), float(y_text), float(when) if when else 0.0)
            except ValueError:
                raise ValueError(f"{part!r} is not x,y@seconds") from None
            if not all(math.isfinite(v) for v in point):
                raise ValueError(f"{part!r} is not a place and a time")
            if points and point[2] < points[-1][2]:
                raise ValueError(f"{part!r} goes back in time")
            points.append(point)
        if not points:
            raise ValueError("a path has at least one point: x,y@seconds")
        return cls(tuple(points))

    @classmethod
    def standing(cls, x: float = 0.0, y: float = 0.0) -> Path:
        return cls(((x, y, 0.0),))

    def at(self, t: float) -> tuple[float, float]:
        first, last = self.points[0], self.points[-1]
        if t <= first[2]:
            return first[0], first[1]
        if t >= last[2]:
            return last[0], last[1]
        for (x0, y0, t0), (x1, y1, t1) in zip(self.points, self.points[1:], strict=False):
            if t0 <= t <= t1:
                share = (t - t0) / (t1 - t0) if t1 > t0 else 1.0
                return x0 + (x1 - x0) * share, y0 + (y1 - y0) * share
        return last[0], last[1]  # pragma: no cover - the loop covers every t in range

    def __str__(self) -> str:
        return " ".join(f"{x:g},{y:g}@{t:g}" for x, y, t in self.points)


@dataclass(frozen=True)
class SimOptions:
    """One sim radio: its air, where its robot walks and from when, how fast the scene runs,
    which sim LAN it is on, its link MTU, an added delay per datagram, and its noise's seed."""

    air: pathlib.Path = field(default_factory=default_air_dir)
    path: Path = field(default_factory=Path.standing)
    #: the scene's start, in wall-clock seconds; None reads scene.json, and failing that, now
    t0: float | None = None
    #: scene seconds a wall-clock second: the demo's --fast runs it faster
    speed: float | None = None
    network: str = "home"
    mtu: int = DEFAULT_MTU
    latency_s: float = 0.0
    model: AirModel = field(default_factory=AirModel)
    seed: int = 0
    #: this radio's name on the air, random unless given
    token: str | None = None


def write_scene(air: pathlib.Path, *, t0: float, speed: float = 1.0) -> None:
    """The scene every radio in `air` keeps time by: its start and its speed."""
    _write_json(air / "scene.json", {"v": 1, "t0": t0, "speed": speed})


def read_scene(air: pathlib.Path) -> tuple[float, float] | None:
    doc = _read_json(air / "scene.json")
    if doc is None or doc.get("v") != 1:
        return None
    t0, speed = doc.get("t0"), doc.get("speed", 1.0)
    if not isinstance(t0, int | float) or not isinstance(speed, int | float) or speed <= 0:
        return None
    return float(t0), float(speed)


# ── files on the air ───────────────────────────────────────────────────────────────────────


def _write_json(path: pathlib.Path, doc: Mapping[str, Any]) -> bool:
    """`doc` at `path`, replaced whole, or False when a reader held it open, which on Windows
    refuses the replace and here costs one advert."""
    tmp = path.with_name(f"{path.name}.{secrets.token_hex(4)}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(json.dumps(doc, separators=(",", ":")).encode("utf-8"))
        os.replace(tmp, path)
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink()
        return False
    return True


async def _unlink(path: pathlib.Path, tries: int = 10) -> None:
    """Remove `path`, trying again for half a second: on Windows a file another radio is
    reading at that moment cannot be removed, and one left behind is heard for `STALE_S`."""
    for _ in range(tries):
        try:
            await asyncio.to_thread(path.unlink, missing_ok=True)
            return
        except OSError:
            await asyncio.sleep(0.05)


def _read_json(path: pathlib.Path) -> dict[str, Any] | None:
    """The JSON object at `path`, or None for one that is missing, being replaced, too large or
    not JSON: any of them is an advert this radio did not hear."""
    try:
        with path.open("rb") as f:
            raw = f.read(MAX_AIR_FILE + 1)
    except OSError:
        return None
    if len(raw) > MAX_AIR_FILE:
        return None
    try:
        doc = json.loads(raw)
    except ValueError:
        return None
    return doc if isinstance(doc, dict) else None


@dataclass(frozen=True)
class AirRecord:
    """Another radio's file, read: its advert, where it stood, its link port and MTU, and when
    it wrote."""

    token: str
    beacon: BleBeacon
    x: float
    y: float
    link: int
    mtu: int
    at: float


def read_radio(air: pathlib.Path, token: str, *, now: float | None = None) -> AirRecord | None:
    """The radio called `token` in `air` as it last wrote, or None when it has not written in
    `STALE_S`, or wrote something that is not a passport advert."""
    if not _TOKEN.match(token):
        return None
    doc = _read_json(air / "radios" / f"{token}.json")
    if doc is None or doc.get("v") != 1:
        return None
    try:
        at, x, y = float(doc["at"]), float(doc["x"]), float(doc["y"])
        link, mtu = int(doc["link"]), int(doc["mtu"])
        ad = bytes.fromhex(str(doc["ad"]))
    except (KeyError, TypeError, ValueError):
        return None
    if (now if now is not None else time.time()) - at > STALE_S:
        return None
    if not (math.isfinite(x) and math.isfinite(y)) or not 0 < link < 65536:
        return None
    beacon = read_advertising_data(ad)
    if beacon is None:
        return None
    return AirRecord(token, beacon, x, y, link, max(MIN_MTU, min(mtu, MAX_DATAGRAM + 3)), at)


# ── a link: a loopback connection carrying GATT's writes and notifications ────────────────


async def _read_op(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    op, length = _OP.unpack(await reader.readexactly(_OP.size))
    if length > MAX_DATAGRAM + 64:
        raise ValueError(f"an operation of {length} bytes on a sim link")
    return op, (await reader.readexactly(length)) if length else b""


class SimPipe(BasePipe):
    """One end of a sim link: the central writes (a write request when it wants to know the
    datagram arrived, a write command when not) and the peripheral notifies, each a datagram of
    at most the link's MTU less 3, in order, with an optional delay each."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        central: bool,
        datagram_size: int,
        address: str,
        peer_token: str,
        latency_s: float = 0.0,
    ) -> None:
        super().__init__(datagram_size)
        self.central = central
        self.address = address
        self.peer_token = peer_token
        self.latency_s = latency_s
        self._reader, self._writer = reader, writer
        self._acks: deque[asyncio.Future[None]] = deque()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._read())

    async def _read(self) -> None:
        reason = "link lost"
        try:
            while True:
                op, payload = await _read_op(self._reader)
                if self.latency_s:
                    await asyncio.sleep(self.latency_s)
                if self.central and op == OP_NOTIFY:
                    self._received(payload)
                elif self.central and op == OP_WRITE_ACK:
                    if self._acks:
                        done = self._acks.popleft()
                        if not done.done():
                            done.set_result(None)
                elif not self.central and op in (OP_WRITE_REQ, OP_WRITE_CMD):
                    if len(payload) > self.datagram_size:
                        reason = "a write longer than the link's MTU allows"
                        return
                    self._received(payload)
                    if op == OP_WRITE_REQ:
                        self._writer.write(_OP.pack(OP_WRITE_ACK, 0))
                else:
                    reason = f"operation {op}, which this end of a sim link never takes"
                    return
        except (asyncio.IncompleteReadError, OSError, ValueError, struct.error):
            return
        finally:
            self._shut(reason)

    async def send(self, datagram: bytes, *, barrier: bool = True) -> None:
        if self.closed is not None:
            raise PipeClosed(self.closed)
        if len(datagram) > self.datagram_size:
            raise ValueError(f"{len(datagram)} bytes, over this link's {self.datagram_size}")
        done: asyncio.Future[None] | None = None
        if self.central:
            op = OP_WRITE_REQ if barrier else OP_WRITE_CMD
            if barrier:
                done = asyncio.get_running_loop().create_future()
                self._acks.append(done)
        else:
            op = OP_NOTIFY  # a notification is never acknowledged, so it is never a barrier
        try:
            self._writer.write(_OP.pack(op, len(datagram)) + datagram)
            await self._writer.drain()
        except (OSError, RuntimeError) as e:
            self._shut("link lost")
            raise PipeClosed(f"link lost: {e}") from e
        if done is not None:
            await done

    async def close(self) -> None:
        self._shut("closed by this end")
        with contextlib.suppress(OSError, RuntimeError):
            await self._writer.wait_closed()

    def _shut(self, reason: str) -> None:
        if not self._ended(reason):
            return
        while self._acks:
            done = self._acks.popleft()
            if not done.done():
                done.set_exception(PipeClosed(reason))
        with contextlib.suppress(OSError, RuntimeError):
            self._writer.close()
        if self._task is not None and self._task is not asyncio.current_task():
            self._task.cancel()


# ── the radio ──────────────────────────────────────────────────────────────────────────────


class SimRadio:
    """A `quackd.radio.Radio` whose air is a directory: it advertises by writing its file,
    hears by reading everybody else's, and takes links on a loopback port."""

    kind = "sim"

    def __init__(self, options: SimOptions | None = None, **overrides: Any) -> None:
        if options is None:
            options = SimOptions(**overrides)
        elif overrides:
            raise TypeError("pass SimOptions or keyword options, not both")
        if not MIN_MTU <= options.mtu <= MAX_DATAGRAM + 3:
            raise ValueError(f"an MTU of {options.mtu}, outside {MIN_MTU} to {MAX_DATAGRAM + 3}")
        self.options = options
        self.token = options.token or secrets.token_hex(6)
        if not _TOKEN.match(self.token):
            raise ValueError(f"{self.token!r} is not a sim radio's name: a-z, 0-9, - and _")
        self.address = SIM_PREFIX + self.token
        self._advert: bytes | None = None
        self._pending_advert: bytes | None = None
        self._advert_set_at = -math.inf
        self._on_sighting: Callable[[Sighting], None] | None = None
        self._on_link: Callable[[Pipe, str], None] | None = None
        self._server: asyncio.Server | None = None
        self._port = 0
        self._tasks: list[asyncio.Task[None]] = []
        self._pipes: set[SimPipe] = set()
        self._out_of_range: dict[SimPipe, float] = {}
        self._rngs: dict[str, random.Random] = {}
        self._t0 = 0.0
        self._speed = 1.0
        self._status = RadioStatus(
            "off", "sim", adapter="sim", address=self.address, detail="not started"
        )

    # ── time and place ──

    def scene_time(self, now: float | None = None) -> float:
        """Seconds since the scene started, at the scene's speed."""
        return ((now if now is not None else time.time()) - self._t0) * self._speed

    def position(self, now: float | None = None) -> tuple[float, float]:
        return self.options.path.at(self.scene_time(now))

    @property
    def speed(self) -> float:
        return self._speed

    # ── Radio ──

    async def start(
        self,
        *,
        advert: bytes,
        on_sighting: Callable[[Sighting], None],
        on_link: Callable[[Pipe, str], None],
    ) -> RadioStatus:
        air = self.options.air
        scene = read_scene(air)
        self._t0 = (
            self.options.t0 if self.options.t0 is not None else (scene[0] if scene else time.time())
        )
        self._speed = self.options.speed or (scene[1] if scene else 1.0)
        self._advert, self._advert_set_at = advert, time.monotonic()
        self._on_sighting, self._on_link = on_sighting, on_link
        try:
            (air / "radios").mkdir(parents=True, exist_ok=True)
            self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        except OSError as e:
            self._status = RadioStatus(
                "missing",
                "sim",
                adapter="sim",
                address=self.address,
                detail=f"the sim air at {air} could not be used: {e}",
                hint="pass --air with a directory this user can write",
            )
            return self._status
        self._port = int(self._server.sockets[0].getsockname()[1])
        self._write()
        loop = asyncio.get_running_loop()
        self._tasks = [loop.create_task(self._advertise()), loop.create_task(self._scan())]
        self._status = RadioStatus(
            "ready",
            "sim",
            adapter="sim",
            address=self.address,
            address_type="random",
            advertising=True,
            scanning=True,
            accepting=True,
            detail=f"the sim air at {air}, on network {self.options.network}",
        )
        return self._status

    async def set_advert(self, advert: bytes) -> None:
        """Take `advert` at the next tick, or once `ADVERT_EVERY_S` (scene time) has passed
        since the last one; until then the newest of those handed in is kept."""
        self._pending_advert = advert

    def _due_advert(self) -> None:
        if self._pending_advert is None:
            return
        if time.monotonic() - self._advert_set_at >= ADVERT_EVERY_S / self._speed:
            self._advert, self._pending_advert = self._pending_advert, None
            self._advert_set_at = time.monotonic()

    async def connect(self, address: str, *, timeout: float) -> Pipe:  # noqa: ASYNC109
        token = address.removeprefix(SIM_PREFIX)
        if not address.startswith(SIM_PREFIX) or not _TOKEN.match(token):
            raise ConnectionError(f"{address!r} is not a sim radio's address")
        record = read_radio(self.options.air, token)
        if record is None:
            raise ConnectionError(f"nothing is advertising at {address} now")
        x, y = self.position()
        if not self.options.model.in_range(math.hypot(record.x - x, record.y - y)):
            raise ConnectionError(f"{address} is out of radio range")
        async with asyncio.timeout(timeout):
            reader, writer = await asyncio.open_connection("127.0.0.1", record.link)
            try:
                hello = self.options.mtu.to_bytes(2, "big") + self.token.encode("ascii")
                writer.write(_OP.pack(OP_SUBSCRIBE, len(hello)) + hello)
                await writer.drain()
                op, payload = await _read_op(reader)
            except (asyncio.IncompleteReadError, ValueError, struct.error) as e:
                # a radio that hung up, as one at its limit does, or said what no link says:
                # no link, which is what a caller of connect is told by a ConnectionError
                writer.close()
                raise ConnectionError(f"{address} hung up before the link opened") from e
            except BaseException:
                writer.close()
                raise
        if op != OP_WRITE_ACK or len(payload) != 2:
            writer.close()
            raise ConnectionError(f"{address} is not a passport link")
        mtu = int.from_bytes(payload, "big")
        pipe = SimPipe(
            reader,
            writer,
            central=True,
            datagram_size=mtu - 3,
            address=address,
            peer_token=token,
            latency_s=self.options.latency_s,
        )
        self._adopt(pipe)
        return pipe

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks = []
        for pipe in list(self._pipes):
            await pipe.close()
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(OSError):
                await self._server.wait_closed()
            self._server = None
        await _unlink(self.options.air / "radios" / f"{self.token}.json")
        self._status = RadioStatus(
            "off", "sim", adapter="sim", address=self.address, detail="stopped"
        )

    def status(self) -> RadioStatus:
        if self._status.state != "ready":
            return self._status
        return RadioStatus(
            self._status.state,
            "sim",
            adapter="sim",
            address=self.address,
            address_type="random",
            advertising=True,
            scanning=True,
            accepting=True,
            links=len(self._pipes),
            detail=self._status.detail,
        )

    # ── the air ──

    def _write(self) -> None:
        if self._advert is None:
            return
        x, y = self.position()
        doc = {
            "v": 1,
            "ad": advertising_data(self._advert).hex(),
            "x": round(x, 3),
            "y": round(y, 3),
            "link": self._port,
            "mtu": self.options.mtu,
            "at": time.time(),
        }
        _write_json(self.options.air / "radios" / f"{self.token}.json", doc)

    async def _advertise(self) -> None:
        while True:
            self._due_advert()
            self._write()
            self._supervise()
            await asyncio.sleep(TICK_S)

    async def _scan(self) -> None:
        radios = self.options.air / "radios"
        while True:
            await asyncio.sleep(TICK_S)
            now, x, y = time.time(), *self.position()
            try:
                names = [p.name for p in radios.iterdir() if p.suffix == ".json"]
            except OSError:
                continue
            for name in names:
                token = name[: -len(".json")]
                if token == self.token:
                    continue
                record = read_radio(self.options.air, token, now=now)
                if record is None:
                    continue
                metres = math.hypot(record.x - x, record.y - y)
                rssi = self.options.model.heard(metres, self._rng(token))
                if rssi is None or self._on_sighting is None:
                    continue
                self._on_sighting(
                    Sighting(
                        record.beacon.beacon_id,
                        record.beacon.flags,
                        rssi,
                        SIM_PREFIX + token,
                        time.monotonic(),
                        true_m=round(metres, 2),
                    )
                )

    def _rng(self, sender: str) -> random.Random:
        """The noise between this radio and `sender`, seeded by both, so a scene with fixed
        tokens and a fixed seed hears the same adverts each time it runs."""
        rng = self._rngs.get(sender)
        if rng is None:
            rng = self._rngs[sender] = random.Random(f"{self.options.seed}/{self.token}/{sender}")
        return rng

    def _supervise(self) -> None:
        """Drop a link whose two robots have been out of range for the supervision timeout, or
        whose other robot stopped advertising that long."""
        now, (x, y) = time.monotonic(), self.position()
        for pipe in list(self._pipes):
            record = read_radio(self.options.air, pipe.peer_token)
            near = record is not None and self.options.model.in_range(
                math.hypot(record.x - x, record.y - y)
            )
            if near:
                self._out_of_range.pop(pipe, None)
                continue
            since = self._out_of_range.setdefault(pipe, now)
            if now - since > self.options.model.supervision_s / self._speed:
                pipe._shut("link lost: out of range")

    # ── links in ──

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if len(self._pipes) >= MAX_CONNECTIONS or self._on_link is None:
            writer.close()
            return
        try:
            async with asyncio.timeout(5.0):
                op, payload = await _read_op(reader)
            token = payload[2:].decode("ascii", errors="replace")
            if op != OP_SUBSCRIBE or len(payload) < 2 or not _TOKEN.match(token):
                raise ValueError("a sim link opens by subscribing, and naming its radio")
            mtu = max(MIN_MTU, min(self.options.mtu, int.from_bytes(payload[:2], "big")))
            writer.write(_OP.pack(OP_WRITE_ACK, 2) + mtu.to_bytes(2, "big"))
            await writer.drain()
        except (OSError, ValueError, TimeoutError, asyncio.IncompleteReadError, struct.error):
            writer.close()
            return
        pipe = SimPipe(
            reader,
            writer,
            central=False,
            datagram_size=mtu - 3,
            address=SIM_PREFIX + token,
            peer_token=token,
            latency_s=self.options.latency_s,
        )
        self._adopt(pipe)
        self._on_link(pipe, pipe.address)

    def _adopt(self, pipe: SimPipe) -> None:
        self._pipes.add(pipe)

        def gone(_reason: str) -> None:
            self._pipes.discard(pipe)
            self._out_of_range.pop(pipe, None)

        pipe.on_close(gone)
        pipe.start()


# ── the sim LAN ────────────────────────────────────────────────────────────────────────────


class SimLan:
    """A LAN of robots on one laptop: a directory per network name, one record per robot, the
    TXT a robot would announce over DNS-SD and where its passport server listens. On the
    network `none` a robot publishes nothing and finds nobody."""

    def __init__(self, air: pathlib.Path, network: str = "home") -> None:
        if network != "none" and not _TOKEN.match(network):
            raise ValueError(f"{network!r} is not a network name: a-z, 0-9, - and _")
        self.air, self.network = air, network
        self._published: pathlib.Path | None = None

    @property
    def directory(self) -> pathlib.Path:
        return self.air / "lan" / self.network

    def publish(self, txt: Mapping[str, str], *, host: str, port: int) -> bool:
        """Announce this robot: its TXT record (its static short id is `id`) and its address.
        Again with a new `bt` after a rotation. False on the network `none`, or when the
        record could not be written this time."""
        if self.network == "none":
            return False
        short_id = str(txt.get("id", ""))
        if not _TOKEN.match(short_id):
            raise ValueError(f"a TXT record's id {short_id!r} is not a short id")
        path = self.directory / f"{short_id}.json"
        written = _write_json(path, {"v": 1, "txt": dict(txt), "host": host, "port": port})
        if written:
            self._published = path
        return written

    def withdraw(self) -> None:
        if self._published is not None:
            with contextlib.suppress(OSError):
                self._published.unlink()
            self._published = None

    def lookup(self, beacon_id: str) -> LanRecord | None:
        """The robot on this network whose record says it advertises `beacon_id` now."""
        if self.network == "none" or not is_beacon_id(beacon_id):
            return None
        try:
            paths = sorted(self.directory.glob("*.json"))
        except OSError:
            return None
        for path in paths:
            doc = _read_json(path)
            if doc is None or doc.get("v") != 1 or not isinstance(doc.get("txt"), dict):
                continue
            txt = doc["txt"]
            if txt.get("bt") != beacon_id:
                continue
            host, port = doc.get("host"), doc.get("port")
            if not isinstance(host, str) or not isinstance(port, int) or not 0 < port < 65536:
                continue
            return LanRecord(host, port, str(txt.get("id", "")), beacon_id, (host,))
        return None


__all__ = [
    "SIM_PREFIX",
    "AirModel",
    "AirRecord",
    "Path",
    "SimLan",
    "SimOptions",
    "SimPipe",
    "SimRadio",
    "default_air_dir",
    "read_radio",
    "read_scene",
    "write_scene",
]
