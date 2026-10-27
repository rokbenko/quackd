"""`mapd`, the Microduck's onboard map, as quackd reaches it.

`MapdClient` speaks to it over two connections of its own, one carrying its stream and one for
asking, and never says `hello`, which it does not answer (`mapd.MAPD_NO_HELLO`). `MapdSource` is
where the robot is by that map: mapd's newest pose, carried on to now by the odometry robotd has
sent since the robot state mapd computed it from, so it moves at robot.state's rate and not at
mapd's, in a frame recognised again from one session to the next by its first stops
(`fingerprint`), since nothing on the wire names a map. The simulators' stand-in keeps to the same
numbers (`sim_map_params`).

Every name it speaks was read from a branch and from no release (`mapd_upstream_api`), so all of it
is UNVERIFIED until a release carries mapd and a robot has answered it.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import sys
import time
import uuid
from collections import deque
from collections.abc import Callable, Coroutine, Sequence
from typing import TYPE_CHECKING, Any

from quackd.space.grid import FREE, OCCUPIED, UNKNOWN, OccupancyGrid
from quackd.space.pose import Fix, FrameRef, Pose2D, wrap_angle
from quackd.space.simmap import SimMapParams
from quackd.space.source import SpatialError, SpatialSourceBase
from quackd.transport.base import TransportError
from quackd_microduck import mapd_upstream_api as mapd
from quackd_microduck import upstream_api as up

if TYPE_CHECKING:
    from quackd.space.odometry import OdometryTrack
    from quackd.space.places import FrameRecord

log = logging.getLogger("quackd.transport.mapd")

STALE_S = max(1.0, 5 * mapd.STREAM_PERIOD_S)
"""How long mapd may send nothing new before its reading is not believed: five of its periods, a
second at least, as a stream's stale window is reckoned. Its probe waits as long for a first."""

FINGERPRINT_STOPS = mapd.AGREE_STOPS
"""How many stops after its first tell one map from another: as many as mapd itself needs to know
a place again (`mapd.MAPD_AGREE`)."""

CELLS = {"?": UNKNOWN, ".": FREE, "#": OCCUPIED}
"""What each character of a `map.grid` means (`mapd.MAPD_GRID_CELLS`)."""

LOST_WORDS = {
    "boot": "it has not recognised anything since it started",
    "carried": "it was carried",
    "bumped": "it was picked up for a moment, and is looking nearby",
    "sat": "it stood up, and is checking it was not carried while seated",
    "fell": "it fell",
    "contradiction": "what it sees stopped matching its map",
}
"""mapd's words for why it does not know where the robot is, in quackd's (`mapd.MAPD_LOST_WORDS`).
One it does not know is quoted and read as lost, as mapd asks: the list grows."""


def default_map_address() -> str:
    """mapd's own socket on the robot (`mapd.MAPD_SOCKET`)."""
    return f"unix://{mapd.MAPD_SOCKET.name}"


def lost_words(word: object) -> str:
    """Why mapd does not know where the robot is, in quackd's words."""
    if isinstance(word, str) and word in LOST_WORDS:
        return LOST_WORDS[word]
    return f"mapd says {word!r}, a word quackd has not read, which it takes for lost"


class MapdAbsent(TransportError):
    """Nothing answers at the address: no socket there, or it refused the connection."""


class MapdRefused(TransportError):
    """Something answered, and not as mapd does: it does not know `map.stream`."""


class MapdSilent(TransportError):
    """It took the connection and sent nothing within the stale window."""


class MapdUnavailable(TransportError):
    """mapd answered, and says it is not mapping at all, and why."""


def _clock(t_ns: object) -> float | None:
    """mapd's `t_ns`, the robot's CLOCK_MONOTONIC, in seconds, as robot.state's is read."""
    if isinstance(t_ns, int | float) and not isinstance(t_ns, bool) and t_ns > 0:
        return float(t_ns) / 1e9
    return None


def _number(value: object) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


def _pose(value: object) -> Pose2D | None:
    """A `MapPose`, `{x, y, yaw}`, or None when it is not one."""
    if not isinstance(value, dict):
        return None
    x, y, yaw = (_number(value.get(k)) for k in ("x", "y", "yaw"))
    if x is None or y is None or yaw is None:
        return None
    return Pose2D(x, y, yaw)


def _count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def grid_of(result: object) -> OccupancyGrid:
    """A `map.grid` answer as quackd's grid (`mapd.MAPD_GRID`), or ValueError saying what in it
    was not a grid."""
    if not isinstance(result, dict):
        raise ValueError(f"map.grid answered with {type(result).__name__}, not a grid")
    res, origin, cells = _number(result.get("res_m")), result.get("origin"), result.get("cells")
    width, height = _count(result.get("width")), _count(result.get("height"))
    if res is None or res <= 0:
        raise ValueError(f"map.grid's res_m is {result.get('res_m')!r}")
    corner = [_number(v) for v in origin] if isinstance(origin, list) else []
    if len(corner) != 2 or corner[0] is None or corner[1] is None:
        raise ValueError(f"map.grid's origin is {origin!r}")
    if width is None or height is None or not isinstance(cells, str):
        raise ValueError("map.grid gave no width, height or cells")
    stops = result.get("keyframes")
    if stops is not None and not isinstance(stops, list):
        raise ValueError(f"map.grid's keyframes are {type(stops).__name__}, not a list of stops")
    keyframes = [p for p in (_pose(k) for k in stops or []) if p is not None]
    return OccupancyGrid.from_text(
        cells,
        width=width,
        height=height,
        res_m=res,
        origin=(corner[0], corner[1]),
        alphabet=CELLS,
        keyframes=keyframes,
        pose=_pose(result.get("pose")),
    )


def first_stops(keyframes: Sequence[Pose2D]) -> tuple[tuple[float, float, float], ...]:
    """What a map is known again by: its first `FINGERPRINT_STOPS` stops after its origin that
    stand further apart, from the origin and from each other, than the correction mapd believes
    (`mapd.MAPD_CORRECTION_GATE`), or nothing until it has that many. Stops in one place, as a
    turn in place makes, tell no map from another, and a map begun with one is every map begun
    with one."""
    if not keyframes:
        return ()
    kept = [keyframes[0]]
    for stop in keyframes[1:]:
        if all(math.hypot(stop.x - k.x, stop.y - k.y) > mapd.CORRECTION_M for k in kept):
            kept.append(stop)
            if len(kept) > FINGERPRINT_STOPS:
                break
    if len(kept) <= FINGERPRINT_STOPS:
        return ()
    return tuple((k.x, k.y, k.yaw) for k in kept[1:])


def same_map(
    known: Sequence[tuple[float, float, float]], seen: Sequence[tuple[float, float, float]]
) -> bool:
    """Whether two maps' first stops after their origins are the same stops: each within the
    correction mapd believes at a stop (`mapd.MAPD_CORRECTION_GATE`)."""
    if len(known) < FINGERPRINT_STOPS or len(seen) < FINGERPRINT_STOPS:
        return False
    gate_rad = math.radians(mapd.CORRECTION_DEG)
    return all(
        math.hypot(a[0] - b[0], a[1] - b[1]) <= mapd.CORRECTION_M
        and abs(wrap_angle(a[2] - b[2])) <= gate_rad
        for a, b in zip(known[:FINGERPRINT_STOPS], seen[:FINGERPRINT_STOPS], strict=True)
    )


class _Link:
    """One line-delimited JSON connection to mapd: a reader, request ids, and each answer handed
    to the request waiting on it. A notification goes to `on_note`, and the end of the link to
    `on_lost`, once. A line too long or not JSON is skipped, as robotd's link skips one."""

    def __init__(
        self,
        address: str,
        *,
        timeout_s: float,
        on_note: Callable[[dict[str, Any]], None],
        on_lost: Callable[[str], None],
    ) -> None:
        self.address = address
        self.timeout_s = timeout_s
        self._on_note = on_note
        self._on_lost = on_lost
        self.lost: str | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._pump: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._next_id = 1

    async def open(self) -> None:
        # robotd's own parse, so an address means the same on both links; imported here, since
        # the simulators import this module for `sim_map_params` alone
        from quackd_microduck.transports.jsonrpc_unix import READER_LIMIT, parse_address

        kind, host, port = parse_address(self.address)
        try:
            if kind == "unix":
                if sys.platform == "win32":
                    raise MapdAbsent(
                        "unix sockets are not available on Windows; forward the robot's map "
                        f"socket with `ssh -L 9871:{mapd.MAPD_SOCKET.name} <robot>` and use "
                        "--map-address tcp://127.0.0.1:9871"
                    )
                reader, writer = await asyncio.open_unix_connection(host, limit=READER_LIMIT)
            else:
                reader, writer = await asyncio.open_connection(host, port, limit=READER_LIMIT)
        except OSError as e:
            raise MapdAbsent(f"nothing answers at {self.address}: {e}") from e
        self._reader, self._writer = reader, writer
        self._pump = asyncio.create_task(self._read_loop(), name="quackd-mapd-link")

    async def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        if self.lost is not None:
            raise TransportError(f"the link to mapd is gone ({self.lost})")
        assert self._writer is not None
        req_id = self._next_id
        self._next_id += 1
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[req_id] = future
        msg: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            msg["params"] = params
        try:
            self._writer.write(json.dumps(msg, separators=(",", ":")).encode() + b"\n")
            await self._writer.drain()
            return await asyncio.wait_for(future, timeout=self.timeout_s)
        except TimeoutError:
            raise TransportError(
                f"mapd gave no answer to {method} within {self.timeout_s:g} s"
            ) from None
        except OSError as e:
            self._lose(f"the link to mapd failed: {e}")
            raise TransportError(f"the link to mapd failed: {e}") from e
        finally:
            self._pending.pop(req_id, None)

    async def _read_loop(self) -> None:
        assert self._reader is not None
        while True:
            try:
                line = await self._reader.readline()
            except ValueError as e:
                log.warning("skipped a line from mapd too long to read: %s", e)
                continue
            except OSError as e:
                self._lose(f"the link to mapd failed: {e}")
                return
            if not line:
                self._lose("mapd closed the connection")
                return
            try:
                self._take(line)
            except Exception as e:  # one line quackd mishandles must not cut the map off
                log.warning("skipped a line from mapd that quackd could not read: %r", e)

    def _take(self, line: bytes) -> None:
        msg = json.loads(line)
        if not isinstance(msg, dict):
            return
        req_id = msg.get("id")
        answered = "result" in msg or "error" in msg
        if isinstance(req_id, int) and not isinstance(req_id, bool) and answered:
            future = self._pending.get(req_id)
            if future is None or future.done():
                return
            error = msg.get("error")
            if error is None:
                future.set_result(msg.get("result"))
                return
            code = error.get("code") if isinstance(error, dict) else None
            text = error.get("message") if isinstance(error, dict) else error
            number = code if isinstance(code, int) and not isinstance(code, bool) else None
            future.set_exception(TransportError(f"{code}: {text}", code=number))
        elif isinstance(msg.get("method"), str):
            self._on_note(msg)

    def _lose(self, why: str) -> None:
        if self.lost is not None:
            return
        self.lost = why
        for future in self._pending.values():
            if not future.done():
                future.set_exception(TransportError(f"the link to mapd is gone ({why})"))
        self._pending.clear()
        self._on_lost(why)

    async def close(self) -> None:
        self.lost = self.lost or "closed"
        if self._pump is not None:
            self._pump.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._pump
            self._pump = None
        if self._writer is not None:
            self._writer.close()
            with contextlib.suppress(Exception):
                await self._writer.wait_closed()
            self._writer = self._reader = None


class MapdClient:
    """quackd's end of mapd: a link its stream comes down, each `map.state` handed to `on_state`,
    and one to ask on, so an answer never waits behind the stream. The end of either is the end of
    both, said to `on_lost` once a connect has finished."""

    def __init__(
        self,
        address: str,
        *,
        request_timeout_s: float,
        on_state: Callable[[dict[str, Any]], None],
        on_lost: Callable[[str], None],
    ) -> None:
        self.address = address
        self.request_timeout_s = request_timeout_s
        self._on_state = on_state
        self._on_lost = on_lost
        self._stream: _Link | None = None
        self._asking: _Link | None = None
        self.status: dict[str, Any] = {}
        """What mapd answered `map.stream` with: where the robot is, how big the map is, and
        `unavailable` when it is not mapping."""

    async def connect(self) -> dict[str, Any]:
        """Open both links and take the stream: the status mapd answered with, once a state has
        come within the stale window. Nothing answering is `MapdAbsent`, an answer that is not
        mapd's `MapdRefused`, and silence `MapdSilent`."""
        first = asyncio.Event()

        def note(msg: dict[str, Any]) -> None:
            params = msg.get("params")
            if msg.get("method") == mapd.MAPD_STATE.name and isinstance(params, dict):
                first.set()
                self._on_state(params)

        stream = _Link(self.address, timeout_s=STALE_S, on_note=note, on_lost=self._lost)
        await stream.open()
        try:
            try:
                status = await stream.request(mapd.MAPD_STREAM.name)
            except TransportError as e:
                if e.code in (up.ERR_METHOD_NOT_FOUND, up.ERR_INVALID_PARAMS):
                    raise MapdRefused(
                        f"what answers at {self.address} does not take {mapd.MAPD_STREAM.name} "
                        f"({e}): quackd read mapd's map.* as API v{mapd.API_VERSION}"
                    ) from None
                if e.code is not None:
                    raise MapdRefused(
                        f"what answers at {self.address} refused {mapd.MAPD_STREAM.name}: {e}"
                    ) from None
                if stream.lost is None:
                    raise MapdSilent(
                        f"{self.address} took the connection and answered nothing in "
                        f"{STALE_S:g} s: {e}"
                    ) from None
                raise MapdAbsent(f"{self.address} hung up: {stream.lost}") from None
            if not isinstance(status, dict):
                raise MapdRefused(
                    f"what answers at {self.address} answered {mapd.MAPD_STREAM.name} with "
                    f"{type(status).__name__}, not mapd's status"
                )
            if isinstance(state := status.get("state"), dict):
                self._on_state(state)
                first.set()
            if not first.is_set():
                try:
                    await asyncio.wait_for(first.wait(), STALE_S)
                except TimeoutError:
                    raise MapdSilent(
                        f"mapd at {self.address} answered and sent no {mapd.MAPD_STATE.name} in "
                        f"{STALE_S:g} s"
                    ) from None
            asking = _Link(
                self.address,
                timeout_s=self.request_timeout_s,
                on_note=lambda _msg: None,
                on_lost=self._lost,
            )
            await asking.open()
        except BaseException:
            await stream.close()
            raise
        if (why := stream.lost or asking.lost) is not None:
            # a link that ended while the other opened would be heard by nobody
            await stream.close()
            await asking.close()
            raise MapdAbsent(f"{self.address} hung up as it was reached: {why}")
        self._stream, self._asking, self.status = stream, asking, status
        return status

    def _lost(self, why: str) -> None:
        if self._stream is None:
            return  # a link of a connect that never finished: its caller hears of it
        self._on_lost(why)

    async def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        if self._asking is None:
            raise TransportError("mapd is not connected")
        return await self._asking.request(method, params)

    async def close(self) -> None:
        stream, asking = self._stream, self._asking
        self._stream = self._asking = None
        for link in (stream, asking):
            if link is not None:
                await link.close()


class MapdSource(SpatialSourceBase):
    """Where the robot is by mapd's map, as quackd reads every source.

    Each `map.state` says where mapd put the robot, `t_ns` the robot state it computed that from.
    The reading is that pose carried on by the odometry robotd has sent since, so it moves at
    robot.state's rate. It says the robot does not know where it is when mapd says so, in quackd's
    words; when a state carries no clock to date it by; when mapd's newest state is older than the
    stale window, by the robot's clock or this machine's; when the link to mapd is gone, which it
    tries to open again in the background and which never falls back to odometry; and for the
    rest of the connection once mapd's clock runs ahead of the robot's while robotd's frames are
    fresh, since then it is not reading the robot robotd is.

    A map is known again by its first stops that stand apart (`first_stops`), read from its grid
    before any frame is named, so the same map is never taken for another for want of them. A
    map with fewer stops than it had, one wiped through quackd, or one whose first stops moved,
    is retired for the rest of the connection: never known again, and said in `retired_frames`
    for its places to be orphaned. After the link comes back, the frame is believed again only
    once the map's stops are read and agree.

    A stop is one mapd has not looked from while mapd says it is collecting one, and from the
    moment odometry has carried the body further than mapd's stillness lets it drift since mapd's
    newest keyframe, until the next: mapd says it is collecting only once its stillness has opened
    one, and a walk waits for the look it stopped for (`settled_fix`), at most `settle_timeout_s`.
    """

    def __init__(
        self,
        address: str,
        *,
        track: OdometryTrack | None,
        request_timeout_s: float,
    ) -> None:
        self.address = address
        self.track = track
        self.client = MapdClient(
            address, request_timeout_s=request_timeout_s, on_state=self.took, on_lost=self._gone
        )
        self.state: dict[str, Any] | None = None
        self.states = 0
        """How many states mapd has sent this connection."""
        self._state_t: float | None = None
        self._state_at: float | None = None
        self._keyframes: int | None = None
        self._robot_t: float | None = None
        """The robot's clock on its newest state frame, the duck on its feet or not."""
        self._robot_at: float | None = None
        """When that frame came, on this machine's clock."""
        self._moved = False
        self._anchor: tuple[Pose2D, int] | None = None
        """Where odometry put the body, and in which of its frames, at mapd's newest keyframe."""
        self._prints: tuple[tuple[float, float, float], ...] = ()
        self._stops_at: int | None = None
        """How many stops the map had when its first stops were read, or None until they are."""
        self._generation = 0
        """One more each time the map may have changed under the source, so a grid asked for
        before cannot teach it the stops of the map after."""
        self._frame: FrameRef | None = None
        self._was: FrameRef | None = None
        """The frame before the link came back, believed again once the map's stops agree."""
        self._retired: set[str] = set()
        self._learning = False
        self._gone_why: str | None = None
        self.off: str | None = None
        """Why mapd is not believed for the rest of the connection, or None."""
        self._odom: deque[tuple[float, Pose2D, int]] = deque()
        self._tasks: set[asyncio.Task[None]] = set()
        self._closed = False

    # ── its life ────────────────────────────────────────────────────────────────────────

    async def start(self) -> dict[str, Any]:
        """Reach mapd and take its stream, and read its map's first stops: the status it
        answered with. Raises as `MapdClient.connect` does, `MapdUnavailable` when mapd says it
        is not mapping, and `MapdRefused` when its grid is not the shape quackd read. A grid that
        does not come in time is asked for again later, and no frame is named before."""
        status = await self.client.connect()
        if why := status.get("unavailable"):
            await self.client.close()
            raise MapdUnavailable(f"mapd at {self.address} is not mapping: {why}")
        generation = self._generation
        try:
            result = await self.client.request(mapd.MAPD_GRID.name)
        except TransportError as e:
            log.warning("mapd at %s gave no map yet (%s); asking again", self.address, e)
            return status
        try:
            grid = grid_of(result)
        except ValueError as e:
            await self.client.close()
            raise MapdRefused(
                f"mapd at {self.address} answered {mapd.MAPD_GRID.name} as quackd did not read "
                f"it: {e}"
            ) from None
        self._learn(grid.keyframes, generation)
        return status

    async def close(self) -> None:
        self._closed = True
        for task in list(self._tasks):
            task.cancel()
        for task in list(self._tasks):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks.clear()
        await self.client.close()

    def _spawn(self, coro: Coroutine[Any, Any, None]) -> None:
        """Run `coro` beside the stream, on the loop the stream runs on; with none, or once
        closed, not at all."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if self._closed or loop is None:
            coro.close()
            return
        task = loop.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _gone(self, why: str) -> None:
        if self._gone_why is not None or self._closed:
            return
        self._gone_why = why
        log.warning("the link to mapd at %s is gone (%s); trying it again", self.address, why)
        self._spawn(self._again())

    async def _again(self) -> None:
        """Open mapd again, a stale window apart, until it answers or the source closes. Its
        frame is held back until the map's stops are read again, since mapd may have begun
        another, and its clock is judged afresh."""
        while not self._closed:
            await asyncio.sleep(STALE_S)
            await self.client.close()
            try:
                status = await self.client.connect()
            except TransportError as e:
                log.debug("mapd at %s is not back yet: %s", self.address, e)
                continue
            if why := status.get("unavailable"):
                await self.client.close()
                self._gone_why = f"mapd is not mapping: {why}"
                continue
            if self._frame is not None:
                self._was, self._frame = self._frame, None
            self._stops_at = None
            self._generation += 1
            self.off = None
            self._gone_why = None
            await self._learn_stops()
            log.warning("mapd at %s answers again", self.address)
            return

    # ── what comes in ───────────────────────────────────────────────────────────────────

    def took(self, state: dict[str, Any]) -> None:
        """One `map.state`."""
        keyframes = _count(state.get("keyframes"))
        before = self._keyframes
        if keyframes is not None and before is not None:
            if keyframes < before:
                self._retire()  # fewer stops than it had: a map wiped or another begun
            elif keyframes > before:
                self._moved = False  # mapd looked from where the body stopped
                self._anchor = (self._odom[-1][1], self._odom[-1][2]) if self._odom else None
        if keyframes is not None:
            self._keyframes = keyframes
            unread = self._frame is None or not self._prints
            if before is not None and keyframes != before and unread:
                self._spawn(self._learn_stops())  # at a stop, as the map is asked for
        t = _clock(state.get("t_ns"))
        robot, robot_at = self._robot_t, self._robot_at
        # judged only on a robot clock that is current: a stalled robotd stream is not mapd's
        fresh = robot_at is not None and time.monotonic() - robot_at <= STALE_S
        ahead = t is not None and robot is not None and fresh and t > robot + STALE_S
        if ahead and self.off is None:
            assert t is not None and robot is not None
            self.off = (
                f"mapd's clock is {t - robot:.1f} s ahead of robotd's, so it is not reading the "
                "robot robotd is"
            )
            log.warning("%s", self.off)
        self.state, self._state_t, self._state_at = state, t, time.monotonic()
        self.states += 1

    def odometry(self, track: OdometryTrack) -> None:
        """The odometry robotd sent in its newest state frame, kept a while on the robot's clock to
        carry mapd's poses on with, and the robot's clock read off it, the duck held or not."""
        reading = track.fix()
        if reading.t is not None:
            if self._robot_t is not None and reading.t < self._robot_t:
                self._odom.clear()  # the robot's clock went back: nothing kept is on it now
            self._robot_t, self._robot_at = reading.t, time.monotonic()
        if reading.pose is None or reading.t is None:
            if track.held is not None:
                self._moved = True  # off its feet: wherever it is set down, mapd has to look
            return
        if self._odom and reading.t <= self._odom[-1][0]:
            return
        self._odom.append((reading.t, reading.pose, track.epoch))
        while self._odom and self._odom[0][0] < reading.t - 2 * STALE_S:
            self._odom.popleft()
        anchor = self._anchor
        if anchor is None:
            self._anchor = (reading.pose, track.epoch)
        elif anchor[1] != track.epoch or _drifted(anchor[0], reading.pose):
            self._moved = True

    async def _learn_stops(self) -> None:
        """Read the map's first stops from its grid, one read at a time."""
        if self._learning:
            return
        self._learning = True
        try:
            with contextlib.suppress(TransportError, SpatialError):
                await self.grid()
        finally:
            self._learning = False

    def _learn(self, keyframes: Sequence[Pose2D], generation: int) -> None:
        """The map's stops, read from a grid asked for at `generation`: nothing when the map may
        have changed since. A map whose first stops are not the ones it had is not the map it
        was."""
        if generation != self._generation:
            return
        stops = first_stops(keyframes)
        moved = bool(self._prints and stops and not same_map(self._prints, stops))
        was, self._was = self._was, None
        if moved:
            if was is not None:
                self._retired.add(was.id)
            self._retire()
            self._prints = stops
        else:
            if was is not None:
                self._frame = was
            if not self._prints:
                self._prints = stops
        self._stops_at = len(keyframes)

    def _retire(self) -> None:
        """The map is not the one it was: its frame is never known again this connection, and
        its stops are read afresh."""
        for frame in (self._frame, self._was):
            if frame is not None:
                self._retired.add(frame.id)
        self._frame = self._was = None
        self._prints, self._stops_at = (), None
        self._generation += 1

    # ── what goes out ───────────────────────────────────────────────────────────────────

    @property
    def fingerprint(self) -> tuple[tuple[float, float, float], ...]:
        return self._prints

    @property
    def retired_frames(self) -> frozenset[str]:
        return frozenset(self._retired)

    def fix(self) -> Fix:
        frame, keyframes = self._frame, self._keyframes
        collecting = self._collecting()

        def lost(why: str) -> Fix:
            return Fix(
                None,
                "map",
                frame,
                lost=why,
                t=self._state_t,
                keyframes=keyframes,
                collecting=collecting,
            )

        if self.off is not None:
            return lost(self.off)
        if self._gone_why is not None:
            return lost(f"the link to mapd is gone ({self._gone_why})")
        state = self.state
        if state is None or self._state_at is None:
            return lost("no reading from mapd yet")
        if word := state.get("lost"):
            return lost(lost_words(word))
        pose = _pose(state.get("pose"))
        if pose is None:
            return lost("mapd gave no pose and no word for why")
        if self._state_t is None:
            return lost("mapd's state carries no clock to date it by")
        quiet = time.monotonic() - self._state_at
        if self._robot_t is not None:
            quiet = max(quiet, self._robot_t - self._state_t)
        if quiet > STALE_S:
            return lost(f"mapd's newest reading is {quiet:.1f} s old")
        carried, t = self._carry(pose, self._state_t)
        return Fix(carried, "map", frame, t=t, keyframes=keyframes, collecting=collecting)

    def _collecting(self) -> bool:
        said = bool(self.state.get("collecting")) if self.state is not None else False
        return said or self._moved

    def _carry(self, pose: Pose2D, t: float) -> tuple[Pose2D, float]:
        """mapd's pose at `t` carried on to the newest odometry, and that odometry's time: as it
        was, at its own time, when no odometry of one frame covers the two."""
        if not self._odom:
            return pose, t
        now_t, now, epoch = self._odom[-1]
        then = self._odometry_at(t, epoch)
        if then is None:
            return pose, t
        return pose.compose(now.relative_to(then)), now_t

    def _odometry_at(self, t: float, epoch: int) -> Pose2D | None:
        """Where odometry put the robot at `t`, between the two readings either side of it in
        `epoch`, or None when none kept is before it in that frame."""
        later: tuple[float, Pose2D, int] | None = None
        for sample in reversed(self._odom):
            if sample[2] != epoch:
                return None
            if sample[0] <= t:
                if later is None:
                    return sample[1]
                (t0, a, _), (t1, b, _) = sample, later
                k = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
                return Pose2D(
                    a.x + (b.x - a.x) * k,
                    a.y + (b.y - a.y) * k,
                    wrap_angle(a.yaw + wrap_angle(b.yaw - a.yaw) * k),
                )
            later = sample
        return None

    def resolve_frame(self, records: Sequence[FrameRecord]) -> FrameRef | None:
        """The map this is, as the place store knows it: the newest frame it remembers, not wiped
        and not retired, whose first stops are this map's; a new one for a map too young to be
        known by its stops; and none at all until the map's stops are read, which this asks for."""
        if self._frame is not None:
            return self._frame
        if self._stops_at is None or (not self._prints and self._stops_at != self._keyframes):
            self._spawn(self._learn_stops())
            return None
        if self._prints:
            for record in reversed(records):
                if (
                    record.kind == "map"
                    and not record.wiped
                    and record.id not in self._retired
                    and same_map(record.fingerprint, self._prints)
                ):
                    self._frame = FrameRef("map", record.id, persistent=True)
                    return self._frame
        self._frame = FrameRef("map", f"map:{uuid.uuid4().hex[:12]}", persistent=True)
        return self._frame

    async def grid(self, res_m: float | None = None) -> OccupancyGrid | None:
        if self._gone_why is not None:
            raise SpatialError(f"the link to mapd is gone ({self._gone_why})")
        generation = self._generation
        params = {"res_m": res_m} if res_m is not None else None
        try:
            result = await self.client.request(mapd.MAPD_GRID.name, params)
            grid = grid_of(result)
        except ValueError as e:
            raise SpatialError(f"mapd's map could not be read: {e}") from None
        except TransportError as e:
            raise SpatialError(f"mapd did not give its map: {e}") from None
        self._learn(grid.keyframes, generation)
        return grid

    async def wipe(self) -> int:
        try:
            result = await self.client.request(mapd.MAPD_WIPE.name)
        except TransportError as e:
            raise SpatialError(f"mapd did not wipe its map: {e}") from None
        self._retire()
        forgot = _count(result.get("keyframes")) if isinstance(result, dict) else None
        return forgot or 0

    @property
    def period_s(self) -> float | None:
        if self.track is not None and self.track.period_s:
            return self.track.period_s
        return mapd.STREAM_PERIOD_S

    @property
    def stop_every_m(self) -> float | None:
        return mapd.CORRECTION_M / math.sin(math.radians(mapd.CORRECTION_DEG))

    @property
    def settle_timeout_s(self) -> float | None:
        """A twist's hold for the body to come to rest, mapd's stillness, and two of its periods
        for the state that says it looked."""
        from quackd.verbs.core import HOLD_TTL_S

        return HOLD_TTL_S + mapd.SETTLE_S + 2 * mapd.STREAM_PERIOD_S

    @property
    def has_grid(self) -> bool:
        return True

    @property
    def can_wipe(self) -> bool:
        return True

    @property
    def view(self) -> tuple[float, float] | None:
        return (math.radians(up.TOF_FOV_DEG), mapd.TOF_MAX_M)

    def describe(self) -> str:
        return f"mapd, the robot's own map, at {self.address} (UNVERIFIED: read at {mapd.BRANCH})"

    def summary(self) -> dict[str, Any]:
        """What doctor and the state say of it."""
        out: dict[str, Any] = {"address": self.address, "states": self.states}
        if self._keyframes is not None:
            out["keyframes"] = self._keyframes
        reading = self.fix()
        out["localized"] = reading.lost is None
        if reading.lost is not None:
            out["lost"] = reading.lost
        if self._state_t is not None and self._robot_t is not None:
            out["behind_robotd_s"] = round(self._robot_t - self._state_t, 3)
        return out


def _drifted(a: Pose2D, b: Pose2D) -> bool:
    """Whether the body has gone from `a` to `b` further than mapd's stillness lets it drift in
    the time it waits for stillness (`mapd.MAPD_SETTLE`)."""
    return (
        math.hypot(b.x - a.x, b.y - a.y) > mapd.STILL_SPEED_MPS * mapd.SETTLE_S
        or abs(wrap_angle(b.yaw - a.yaw)) > mapd.STILL_TURN_RPS * mapd.SETTLE_S
    )


def sim_map_params(footprint_m: float) -> SimMapParams:
    """The real mapper's numbers for a simulated one, and `footprint_m`, the simulator's own
    body radius, which the real mapper does not publish."""
    return SimMapParams(
        still_s=mapd.SETTLE_S,
        still_speed_mps=mapd.STILL_SPEED_MPS,
        still_turn_rps=mapd.STILL_TURN_RPS,
        period_s=mapd.STREAM_PERIOD_S,
        reloc_stops=mapd.AGREE_STOPS,
        gate_m=mapd.CORRECTION_M,
        gate_rad=math.radians(mapd.CORRECTION_DEG),
        res_m=mapd.GRID_RES_M,
        tof_fov_rad=math.radians(up.TOF_FOV_DEG),
        tof_min_m=mapd.TOF_MIN_M,
        tof_range_m=mapd.TOF_MAX_M,
        footprint_m=footprint_m,
    )
