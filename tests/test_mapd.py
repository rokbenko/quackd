"""The Microduck's own map, `mapd`, as quackd reaches it beside robotd
(`quackd_microduck.transports.mapd`): a fake of each on a TCP port, as a tunnel gives them, and
the source that walks by the map, read without either."""

from __future__ import annotations

import asyncio
import contextlib
import math
import socket
import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest

from quackd.space.odometry import OdometrySource, OdometryTrack
from quackd.space.places import FrameRecord, Place, PlaceBook, PlaceStore
from quackd.space.pose import Pose2D
from quackd.space.source import SpatialSourceBase
from quackd.transport.base import Intent
from quackd.verbs.places import PLACE_VERBS, GoToPlaceParams
from quackd.verbs.registry import VerbContext
from quackd_microduck import MicroduckAdapter
from quackd_microduck import mapd_upstream_api as mapd_api
from quackd_microduck.transports.jsonrpc_unix import JsonRpcUnixTransport
from quackd_microduck.transports.mapd import (
    FINGERPRINT_STOPS,
    LOST_WORDS,
    STALE_S,
    MapdSource,
    lost_words,
    same_map,
)
from tests.fake_mapd import FakeMapd
from tests.test_jsonrpc import FakeRobotd


@pytest.fixture
async def robotd() -> Any:
    fake = FakeRobotd()
    await fake.start()
    yield fake
    await fake.stop()


@pytest.fixture
async def mapd() -> Any:
    fake = FakeMapd()
    await fake.start()
    yield fake
    await fake.stop()


async def _eventually(check: Any, what: str, wait_s: float = 30.0) -> None:
    deadline = time.monotonic() + wait_s
    while not check():
        if time.monotonic() > deadline:
            raise AssertionError(f"{what} did not happen within {wait_s:g} s")
        await asyncio.sleep(0.01)


async def _connected(robotd: FakeRobotd, map_address: str | None) -> JsonRpcUnixTransport:
    t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{robotd.port}", map_address=map_address)
    await t.connect()
    return t


def _free_port() -> int:
    """A port nothing listens on: bound, read and let go."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


# ── beside robotd ───────────────────────────────────────────────────────────────────────


async def test_the_duck_walks_by_mapds_map_and_mapd_is_never_said_hello_to(
    robotd: FakeRobotd, mapd: FakeMapd
) -> None:
    """mapd answers every call it does not know with METHOD_NOT_FOUND, `hello` among them, so it is
    checked for with its stream and nothing else."""
    t = await _connected(robotd, mapd.address)
    try:
        assert isinstance(t.spatial, MapdSource)
        assert "hello" not in mapd.methods()
        assert mapd.methods()[0] == mapd_api.MAPD_STREAM.name
        where = (await t.get_state()).extras["where"]
        assert where["kind"] == "map" and where["keyframes"] == len(mapd.keyframes)
        assert t.mapd_report()["state"] == "answered" and t.map_warning is None
    finally:
        await t.close()


async def test_no_mapd_at_an_address_nobody_named_leaves_odometry_without_a_word(
    robotd: FakeRobotd,
) -> None:
    t = await _connected(robotd, None)
    try:
        assert isinstance(t.spatial, OdometrySource)
        assert t.mapd_report()["state"] == "not probed" and t.map_warning is None
    finally:
        await t.close()


async def test_a_named_address_nothing_answers_at_leaves_odometry_and_says_so(
    robotd: FakeRobotd,
) -> None:
    address = f"tcp://127.0.0.1:{_free_port()}"
    t = await _connected(robotd, address)
    try:
        assert isinstance(t.spatial, OdometrySource)
        report = t.mapd_report()
        assert report["state"] == "absent" and report["given"] is True
        assert t.map_warning is not None and f"--map-address {address}" in t.map_warning
        # and robotd's link goes on as if nothing had been tried
        frames = robotd.frames_sent
        await _eventually(lambda: robotd.frames_sent > frames + 2, "robotd's frames going on")
        age = t.state_age_s()
        assert age is not None and age < 1.0
    finally:
        await t.close()


async def test_something_that_does_not_take_mapds_stream_leaves_odometry_and_names_both_apis(
    robotd: FakeRobotd, mapd: FakeMapd
) -> None:
    mapd.missing = {mapd_api.MAPD_STREAM.name}
    t = await _connected(robotd, mapd.address)
    try:
        assert isinstance(t.spatial, OdometrySource)
        assert t.mapd_report()["state"] == "refused"
        warning = t.map_warning or ""
        assert mapd_api.MAPD_STREAM.name in warning
        assert f"API v{mapd_api.API_VERSION}" in warning
        assert f"API v{robotd.api_version}" in warning
        frames = robotd.frames_sent
        await _eventually(lambda: robotd.frames_sent > frames + 2, "robotd's frames going on")
    finally:
        await t.close()


async def test_a_mapd_that_is_not_mapping_leaves_odometry_and_says_why(
    robotd: FakeRobotd, mapd: FakeMapd
) -> None:
    mapd.unavailable = "no ToF frames from tofd"
    t = await _connected(robotd, mapd.address)
    try:
        assert isinstance(t.spatial, OdometrySource)
        assert t.mapd_report()["state"] == "unavailable"
        assert "no ToF frames from tofd" in (t.map_warning or "")
    finally:
        await t.close()


async def test_a_slow_mapd_never_holds_robot_move_back(robotd: FakeRobotd, mapd: FakeMapd) -> None:
    """mapd is asked on links of its own: an answer it sits on while a walk goes on never reaches
    robotd's, where `robot.move` goes."""
    t = await _connected(robotd, mapd.address)
    try:
        assert isinstance(t.spatial, MapdSource)
        mapd.slow_s = 2 * t.request_timeout_s
        asking = asyncio.create_task(t.spatial.grid())
        await asyncio.sleep(0.05)  # the map asked for, and not answering
        took = []
        for _ in range(10):
            started = time.monotonic()
            await t.send_intent(Intent.move(0.1, 0.0, 0.0))
            took.append(time.monotonic() - started)
            await asyncio.sleep(0.1)
        assert not asking.done(), "mapd answered before the moves were sent"
        assert max(took) < t.request_timeout_s / 2, took
        await _eventually(lambda: robotd.twist[0] == pytest.approx(0.1), "robotd taking the twist")
        asking.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await asking
    finally:
        await t.close()


async def test_mapd_gone_mid_walk_ends_the_walk_lost_and_never_walks_on_by_odometry(
    robotd: FakeRobotd, mapd: FakeMapd
) -> None:
    t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{robotd.port}", map_address=mapd.address)
    adapter = MicroduckAdapter(t)
    try:
        manifest = await adapter.connect()
        source = t.spatial
        assert isinstance(source, MapdSource)
        book = PlaceBook(PlaceStore("t", persist=False), source)
        frame = book.frame()
        assert frame is not None
        book.store.mark(Place("ahead", 1.0, 0.0, 0.0, frame.id, frame.kind, 0.0))
        ctx = VerbContext(transport=adapter, manifest=manifest, places=book)
        walk = asyncio.create_task(
            PLACE_VERBS["go_to_place"].execute(ctx, GoToPlaceParams(name="ahead", face=False))
        )
        await _eventually(lambda: robotd.twist[0] > 0, "the walk's first twist")
        mapd.hang_up()
        result = await asyncio.wait_for(walk, 30)
        assert not result.ok and result.data["outcome"] == "lost", result.summary
        assert "the link to mapd is gone" in result.summary
        assert t.spatial is source, "it fell back to odometry"
        assert robotd.twist == [0.0, 0.0, 0.0], "the body was left walking"
    finally:
        await adapter.close()


async def test_mapd_back_after_a_drop_is_walked_by_again_in_the_map_it_was(
    robotd: FakeRobotd, mapd: FakeMapd
) -> None:
    t = await _connected(robotd, mapd.address)
    try:
        source = t.spatial
        assert isinstance(source, MapdSource)
        book = PlaceBook(PlaceStore("t", persist=False), source)
        before = book.frame()
        assert before is not None and source.fingerprint
        mapd.hang_up()
        await _eventually(lambda: source.fix().lost is not None, "the drop being seen")
        await _eventually(
            lambda: source.fix().lost is None and book.frame() is not None,
            "mapd answering again, its map read",
            15,
        )
        assert book.frame() == before, "the same map was taken for another"
    finally:
        await t.close()


# ── the reading, without sockets ────────────────────────────────────────────────────────


def _source() -> tuple[MapdSource, OdometryTrack]:
    track = OdometryTrack("odom:test")
    return MapdSource("tcp://127.0.0.1:1", track=track, request_timeout_s=1.0), track


def _read(source: MapdSource, stops: list[tuple[float, float, float]]) -> None:
    """The map's stops, as a grid read from mapd would hand them to the source."""
    source._learn([Pose2D(*stop) for stop in stops], source._generation)


def _odometry(source: MapdSource, track: OdometryTrack, x: float, y: float, yaw: float, t: float):
    track.update(x, y, yaw, t)
    source.odometry(track)


def test_the_reading_is_mapds_pose_carried_on_by_the_odometry_since() -> None:
    """mapd's pose is a robot state old: carried on by what odometry walked since that state's
    time, in mapd's frame, it moves at robot.state's rate."""
    source, track = _source()
    for i in range(11):
        _odometry(source, track, 0.02 * i, 0.0, 0.0, 10.0 + 0.1 * i)  # forward, 0.2 m in 1 s
    mapd_t = 10.5
    source.took(
        {
            "t_ns": int(mapd_t * 1e9),
            "pose": {"x": 1.0, "y": 0.0, "yaw": math.pi / 2},
            "keyframes": 3,
            "collecting": False,
        }
    )
    fix = source.fix()
    assert fix.lost is None and fix.pose is not None
    # 0.1 m walked forward after mapd's state, which faces +y in mapd's frame
    assert fix.pose.x == pytest.approx(1.0, abs=1e-9)
    assert fix.pose.y == pytest.approx(0.1, abs=1e-9)
    assert fix.pose.yaw == pytest.approx(math.pi / 2)
    assert fix.t == pytest.approx(11.0), "dated by the odometry it was carried to"


def test_odometry_begun_again_since_mapds_state_carries_nothing() -> None:
    source, track = _source()
    _odometry(source, track, 0.0, 0.0, 0.0, 10.0)
    _odometry(source, track, 0.05, 0.0, 0.0, 10.1)
    track.update(0.05, 0.0, 0.0, 10.2, held="picked up")
    _odometry(source, track, 0.3, 0.0, 0.0, 10.4)  # back on its feet, in another epoch
    source.took({"t_ns": int(10.1e9), "pose": {"x": 2.0, "y": 1.0, "yaw": 0.0}, "keyframes": 3})
    fix = source.fix()
    assert fix.pose is not None and (fix.pose.x, fix.pose.y) == (2.0, 1.0)


def test_mapds_words_for_not_knowing_are_quackds_and_an_unknown_one_is_lost_too() -> None:
    assert set(LOST_WORDS) == set(mapd_api.MAPD_LOST_WORDS.name.split(", "))
    source, track = _source()
    _odometry(source, track, 0.0, 0.0, 0.0, 10.0)
    for word in LOST_WORDS:
        source.took({"t_ns": int(10.0e9), "lost": word, "keyframes": 3})
        assert source.fix().lost == LOST_WORDS[word]
    source.took({"t_ns": int(10.0e9), "lost": "levitating", "keyframes": 3})
    assert source.fix().lost == lost_words("levitating")
    assert "'levitating'" in lost_words("levitating")


def test_a_state_with_neither_a_pose_nor_a_word_is_lost() -> None:
    source, track = _source()
    _odometry(source, track, 0.0, 0.0, 0.0, 10.0)
    source.took({"t_ns": int(10.0e9), "keyframes": 3})
    assert source.fix().lost is not None and source.fix().pose is None


def test_a_state_older_than_the_stale_window_by_the_robots_clock_is_not_believed() -> None:
    source, track = _source()
    _odometry(source, track, 0.0, 0.0, 0.0, 10.0)
    source.took({"t_ns": int(10.0e9), "pose": {"x": 0.0, "y": 0.0, "yaw": 0.0}, "keyframes": 3})
    assert source.fix().lost is None
    _odometry(source, track, 0.0, 0.0, 0.0, 10.0 + STALE_S + 0.1)
    assert "mapd's newest reading is" in (source.fix().lost or "")


def test_a_mapd_whose_clock_runs_ahead_of_robotds_is_not_believed_again() -> None:
    source, track = _source()
    _odometry(source, track, 0.0, 0.0, 0.0, 10.0)
    ahead = int((10.0 + 2 * STALE_S) * 1e9)
    source.took({"t_ns": ahead, "pose": {"x": 0.0, "y": 0.0, "yaw": 0.0}, "keyframes": 3})
    assert "ahead of robotd's" in (source.fix().lost or "")
    source.took({"t_ns": int(10.0e9), "pose": {"x": 0.0, "y": 0.0, "yaw": 0.0}, "keyframes": 3})
    assert "ahead of robotd's" in (source.fix().lost or ""), "it was believed again"


def test_a_stop_is_one_to_look_from_once_odometry_moves_until_mapd_takes_a_keyframe() -> None:
    """mapd says it is collecting only once its stillness has opened a keyframe, so a walk that
    stops reads its stop as one still to be looked from from the moment it moved."""
    source, track = _source()
    _odometry(source, track, 0.0, 0.0, 0.0, 10.0)
    source.took({"t_ns": int(10.0e9), "pose": {"x": 0.0, "y": 0.0, "yaw": 0.0}, "keyframes": 3})
    assert source.fix().collecting is False
    _odometry(source, track, 0.05, 0.0, 0.0, 10.1)  # faster than mapd's stillness
    _odometry(source, track, 0.05, 0.0, 0.0, 10.2)
    source.took({"t_ns": int(10.2e9), "pose": {"x": 0.05, "y": 0.0, "yaw": 0.0}, "keyframes": 3})
    assert source.fix().collecting is True
    source.took({"t_ns": int(10.6e9), "pose": {"x": 0.05, "y": 0.0, "yaw": 0.0}, "keyframes": 4})
    assert source.fix().collecting is False


def test_fewer_stops_than_before_is_another_map() -> None:
    source, track = _source()
    _odometry(source, track, 0.0, 0.0, 0.0, 10.0)
    source.took({"t_ns": int(10.0e9), "pose": {"x": 0.0, "y": 0.0, "yaw": 0.0}, "keyframes": 3})
    _read(source, [(0.0, 0.0, 0.0), (0.8, 0.1, 0.3), (1.4, -0.2, -0.5)])
    first = source.resolve_frame([])
    source.took({"t_ns": int(10.1e9), "pose": {"x": 0.0, "y": 0.0, "yaw": 0.0}, "keyframes": 1})
    assert source.fix().frame is None
    assert source.resolve_frame([]) is None, "a frame named before the new map's stops were read"
    _read(source, [(0.0, 0.0, 0.0)])
    second = source.resolve_frame([_record(first.id, source.fingerprint)])  # type: ignore[union-attr]
    assert first is not None and second is not None and first.id != second.id
    assert first.id in source.retired_frames


# ── a map known again ───────────────────────────────────────────────────────────────────


def _record(fid: str, prints: tuple[tuple[float, float, float], ...], wiped: bool = False):
    return FrameRecord(fid, "map", True, 0.0, fingerprint=prints, wiped=wiped)


def test_a_map_is_known_again_by_its_first_stops_within_the_correction_mapd_believes() -> None:
    gate = mapd_api.CORRECTION_M
    known = ((0.8, 0.1, 0.3), (1.4, -0.2, -0.5))
    near = tuple((x + gate / 2, y, yaw) for x, y, yaw in known)
    far = tuple((x + 2 * gate, y, yaw) for x, y, yaw in known)
    assert len(known) == FINGERPRINT_STOPS
    assert same_map(known, near) and not same_map(known, far)
    assert not same_map(known[:1], known[:1]), "too few stops to tell one map from another"
    origin = (0.0, 0.0, 0.0)
    source, _track = _source()
    _read(source, [origin, *near])
    assert source.resolve_frame([_record("map:kitchen", known)]).id == "map:kitchen"  # type: ignore[union-attr]
    source, _track = _source()
    _read(source, [origin, *near])
    frame = source.resolve_frame([_record("map:kitchen", known, wiped=True)])
    assert frame is not None and frame.id != "map:kitchen", "a wiped map came back"


class _Printed(SpatialSourceBase):
    """A source whose map is known by `prints` once it has them."""

    def __init__(self) -> None:
        self.prints: tuple[tuple[float, float, float], ...] = ()

    @property
    def fingerprint(self) -> tuple[tuple[float, float, float], ...]:
        return self.prints

    def resolve_frame(self, records: Any) -> Any:
        from quackd.space.pose import FrameRef

        return FrameRef("map", "map:new", persistent=True)


def test_the_place_store_keeps_a_maps_first_stops_once_the_source_has_them(tmp_path: Any) -> None:
    source = _Printed()
    book = PlaceBook(PlaceStore("duck", base_dir=tmp_path), source)
    book.frame()
    record = book.store.frame_record("map:new")
    assert record is not None and record.fingerprint == ()
    source.prints = ((0.8, 0.1, 0.3), (1.4, -0.2, -0.5))
    book.frame()
    again = PlaceStore("duck", base_dir=tmp_path).frame_record("map:new")
    assert again is not None and again.fingerprint == source.prints
    assert again.ts == record.ts


# ── where it is configured ──────────────────────────────────────────────────────────────


def test_a_map_address_is_kept_with_the_robot_and_left_out_of_the_file_while_empty(
    tmp_path: Any,
) -> None:
    import json

    from quackd.registry import Registry, RobotEntry

    reg = Registry(tmp_path)
    reg.add_robot(RobotEntry(name="duck", spec="microduck:jsonrpc", map_address="tcp://h:9871"))
    reg.add_robot(RobotEntry(name="other", spec="microduck:jsonrpc"))
    stored = json.loads((tmp_path / "robots.json").read_text(encoding="utf-8"))
    robots = stored.get("robots", stored)
    assert robots["duck"]["map_address"] == "tcp://h:9871"
    assert "map_address" not in robots["other"]
    entry = reg.get_robot("duck")
    assert entry is not None
    assert entry.adapter_kwargs()["map_address"] == "tcp://h:9871"
    assert entry.adapter_kwargs(map_address="tcp://other:1")["map_address"] == "tcp://other:1"


def test_a_map_address_is_refused_by_name_where_nothing_reads_one() -> None:
    from quackd.adapters.base import AdapterError
    from quackd.adapters.factory import make_adapter

    with pytest.raises(AdapterError, match="keeps its own map of the room"):
        make_adapter("microduck:sim2d", map_address="tcp://127.0.0.1:9871")
    with pytest.raises(AdapterError, match="--map-address is for microduck:jsonrpc"):
        make_adapter("microduck:mock", map_address="tcp://127.0.0.1:9871")
    with pytest.raises(AdapterError, match="reads no map the robot keeps"):
        make_adapter("lerobot:mock", map_address="tcp://127.0.0.1:9871")


# ── doctor ──────────────────────────────────────────────────────────────────────────────


@contextlib.contextmanager
def _fakes_on_their_own_loop() -> Iterator[tuple[FakeRobotd, FakeMapd]]:
    """Both fakes serving from a loop in a thread of their own, for `doctor.probe`, which runs a
    loop of its own."""
    loop = asyncio.new_event_loop()
    started: list[Any] = []
    ready = threading.Event()

    def serve() -> None:
        asyncio.set_event_loop(loop)
        robot, map_ = FakeRobotd(), FakeMapd()
        loop.run_until_complete(robot.start())
        loop.run_until_complete(map_.start())
        started.extend([robot, map_])
        ready.set()
        loop.run_forever()

    thread = threading.Thread(target=serve, name="fake-robotd-and-mapd", daemon=True)
    thread.start()
    assert ready.wait(30), "the fakes never started"
    try:
        yield started[0], started[1]
    finally:
        for fake in started:
            asyncio.run_coroutine_threadsafe(fake.stop(), loop).result(30)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(30)
        loop.close()


def _probe(map_address: str | None) -> Any:
    from quackd import doctor
    from quackd_microduck import describe

    with _fakes_on_their_own_loop() as (robot, map_):
        return doctor.probe(
            "microduck:jsonrpc",
            describe("jsonrpc"),
            f"tcp://127.0.0.1:{robot.port}",
            None,
            None,
            map_address=map_.address if map_address == "fake" else map_address,
        )


def test_doctor_says_mapd_answered_and_that_it_is_unverified() -> None:
    report = _probe("fake")
    assert report.ok, report
    row = next(r for r in report.rows if r.what == "mapd")
    assert row.value.startswith("answered at ") and row.state == "ok"
    assert any("UNVERIFIED" in a for a in report.advisories)


def test_doctor_fails_a_map_address_that_does_not_answer() -> None:
    report = _probe(f"tcp://127.0.0.1:{_free_port()}")
    assert not report.ok
    row = next(r for r in report.rows if r.what == "mapd")
    assert row.value.startswith("absent at ") and row.state == "fail"
    assert any("contact odometry" in a for a in report.advisories)


# ── found in review ─────────────────────────────────────────────────────────────────────

#: a map begun with a turn in place: its first stops stand where it began
LIVING_ROOM = [
    {"x": 0.0, "y": 0.0, "yaw": 0.0},
    {"x": 0.01, "y": 0.0, "yaw": math.pi / 4},
    {"x": 0.02, "y": 0.01, "yaw": math.pi / 2},
    {"x": 1.5, "y": 0.3, "yaw": math.pi / 2},
]
#: another room, begun the same way
KITCHEN = [
    {"x": 0.0, "y": 0.0, "yaw": 0.0},
    {"x": 0.0, "y": 0.02, "yaw": math.pi / 4 + 0.02},
    {"x": 0.03, "y": 0.0, "yaw": math.pi / 2 - 0.03},
    {"x": -1.0, "y": 2.0, "yaw": 2.0},
]


async def _session(
    fake: FakeMapd, home: Any, timeout_s: float = 1.0
) -> tuple[MapdSource, PlaceBook]:
    source = MapdSource(fake.address, track=None, request_timeout_s=timeout_s)
    await source.start()
    return source, PlaceBook(PlaceStore("duck", base_dir=home), source)


async def test_a_map_begun_with_a_turn_in_place_is_not_another_map_begun_so(tmp_path: Any) -> None:
    """Stops in one place tell no map from another: two of them were every map's fingerprint
    whose walk began with a scan, and the living room's door was there in the kitchen."""
    fake = FakeMapd()
    fake.keyframes = [dict(k) for k in LIVING_ROOM]
    await fake.start()
    try:
        first, book = await _session(fake, tmp_path)
        f1 = book.frame()
        assert f1 is not None
        book.store.mark(Place("door", 2.0, 1.0, 0.0, f1.id, "map", 0.0))
        await first.close()
        fake.keyframes = [dict(k) for k in KITCHEN]  # another map between sessions
        second, book = await _session(fake, tmp_path)
        door = book.view(book.store.get("door"))  # type: ignore[arg-type]
        await second.close()
        assert door.status == "orphaned", f"the living room's door is {door.status} in the kitchen"
    finally:
        await fake.stop()


async def test_a_map_quackd_saw_replaced_is_never_known_again_and_its_places_say_so(
    tmp_path: Any,
) -> None:
    fake = FakeMapd()
    fake.keyframes = [dict(k) for k in LIVING_ROOM]
    await fake.start()
    try:
        source, book = await _session(fake, tmp_path)
        f1 = book.frame()
        assert f1 is not None
        book.store.mark(Place("door", 2.0, 1.0, 0.0, f1.id, "map", 0.0))
        fake.keyframes = [dict(KITCHEN[0])]  # wiped from elsewhere: a new map where it stands
        await _eventually(lambda: f1.id in source.retired_frames, "the drop being seen")
        for k in KITCHEN[1:]:
            fake.keyframes.append(dict(k))
            await asyncio.sleep(0.6)
        f2 = book.frame()
        door = book.view(book.store.get("door"))  # type: ignore[arg-type]
        await source.close()
        assert f2 is None or f2.id != f1.id, "the map quackd saw replaced is back"
        assert door.status == "orphaned" and "wiped" in (door.reason or ""), door
    finally:
        await fake.stop()


def test_a_pickup_longer_than_the_stale_window_leaves_mapd_believed_once_it_knows_again() -> None:
    """While the duck is held its odometry has no pose, and mapd's clock was compared with the
    odometry's last time: a pick-up past the stale window turned mapd off for the connection."""
    source, track = _source()
    t = 100.0
    for _ in range(5):
        _odometry(source, track, 0.0, 0.0, 0.0, t)
        t += 0.1
    source.took({"t_ns": int((t - 0.1) * 1e9), "pose": {"x": 1, "y": 1, "yaw": 0}, "keyframes": 5})
    for i in range(20):  # held for two seconds, robot.state at 10 Hz, mapd at 4
        track.update(0.0, 0.0, 0.0, t, held="picked up")
        source.odometry(track)
        if i % 2 == 0:
            source.took({"t_ns": int(t * 1e9), "lost": "carried", "keyframes": 5})
        t += 0.1
    for i in range(20):  # set down, and known again after two stops
        _odometry(source, track, 0.0, 0.0, 0.0, t)
        if i % 2 == 0:
            source.took({"t_ns": int(t * 1e9), "pose": {"x": 1, "y": 1, "yaw": 0}, "keyframes": 7})
        t += 0.1
    assert source.off is None and source.fix().lost is None, source.fix().lost


async def test_a_duck_held_two_seconds_beside_mapd_is_walked_by_its_map_again(
    robotd: FakeRobotd, mapd: FakeMapd
) -> None:
    t = await _connected(robotd, mapd.address)
    try:
        source = t.spatial
        assert isinstance(source, MapdSource)
        await _eventually(lambda: source.fix().lost is None, "mapd knowing where it is")
        robotd.picked_up, mapd.lost = True, "carried"
        await asyncio.sleep(2 * STALE_S)
        robotd.picked_up, mapd.lost = False, None
        await _eventually(lambda: source.fix().lost is None, "mapd believed again")
        assert source.off is None
    finally:
        await t.close()


async def test_one_slow_grid_at_connect_never_takes_the_same_map_for_another(tmp_path: Any) -> None:
    fake = FakeMapd()
    await fake.start()
    try:
        first, book = await _session(fake, tmp_path, 0.5)
        f1 = book.frame()
        assert f1 is not None and first.fingerprint
        book.store.mark(Place("sofa", 0.5, 0.5, 0.0, f1.id, "map", 0.0))
        await first.close()
        fake.slow_s = 1.0  # one map.grid slower than the request timeout, at connect
        second, book = await _session(fake, tmp_path, 0.5)
        fake.slow_s = 0.0
        assert book.frame() is None, "a frame was named before the map's stops were read"
        await _eventually(lambda: book.frame() is not None, "the map's stops being read")
        sofa = book.view(book.store.get("sofa"))  # type: ignore[arg-type]
        await second.close()
        assert book.frame() == f1 and sofa.status == "here", sofa
    finally:
        await fake.stop()


async def test_an_observation_while_the_link_comes_back_does_not_name_another_map(
    tmp_path: Any,
) -> None:
    fake = FakeMapd()
    await fake.start()
    try:
        source, book = await _session(fake, tmp_path, 3.0)
        before = book.frame()
        assert before is not None
        fake.slow_s = 1.0  # the grid after the link comes back takes a second
        fake.hang_up()
        await _eventually(lambda: source.fix().lost is not None, "the drop being seen")
        await _eventually(lambda: source.fix().lost is None, "the link back", 15)
        during = book.frame()  # an agent turn's observation in that second
        await _eventually(lambda: book.frame() is not None, "the map's stops being read")
        await source.close()
        assert during in (None, before) and book.frame() == before
    finally:
        await fake.stop()


async def test_a_young_maps_frame_outlives_a_blip_in_the_link(tmp_path: Any) -> None:
    fake = FakeMapd()
    fake.keyframes = fake.keyframes[:2]  # its origin and one stop: too young to be known by them
    await fake.start()
    try:
        source, book = await _session(fake, tmp_path)
        before = book.frame()
        assert before is not None
        fake.hang_up()  # mapd restarts with the same map
        await _eventually(lambda: source.fix().lost is not None, "the drop being seen")
        await _eventually(
            lambda: source.fix().lost is None and book.frame() is not None, "the link back", 15
        )
        await source.close()
        assert book.frame() == before, "the same young map is another after a blip"
    finally:
        await fake.stop()


async def test_the_first_mark_place_beside_mapd_is_kept(robotd: FakeRobotd, mapd: FakeMapd) -> None:
    """The reading carried no frame until something had asked for one, and the place book read
    the reading first, so the first mark of every session was refused as no reading yet."""
    from quackd.verbs.places import MarkPlaceParams

    t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{robotd.port}", map_address=mapd.address)
    adapter = MicroduckAdapter(t)
    try:
        manifest = await adapter.connect()
        assert isinstance(t.spatial, MapdSource)
        book = PlaceBook(PlaceStore("duck", persist=False), t.spatial)
        ctx = VerbContext(transport=adapter, manifest=manifest, places=book)
        first = await PLACE_VERBS["mark_place"].execute(ctx, MarkPlaceParams(name="sofa"))
        assert first.ok, first.summary
    finally:
        await adapter.close()


class _OddGrid(FakeMapd):
    def grid(self) -> dict[str, Any]:
        return {**super().grid(), "keyframes": len(self.keyframes)}  # a count, not the stops


def _links() -> list[asyncio.Task[Any]]:
    return [t for t in asyncio.all_tasks() if t.get_name() == "quackd-mapd-link" and not t.done()]


async def test_a_grid_that_is_not_mapds_leaves_robotd_connected_by_odometry_and_no_link_open(
    robotd: FakeRobotd,
) -> None:
    odd = _OddGrid()
    await odd.start()
    try:
        t = await _connected(robotd, odd.address)
        try:
            assert isinstance(t.spatial, OdometrySource)
            assert t.mapd_report()["state"] == "refused"
            assert f"answered {mapd_api.MAPD_GRID.name} as quackd did not read it" in (
                t.map_warning or ""
            )
            assert not _links(), "mapd's links outlived the probe that refused it"
        finally:
            await t.close()
    finally:
        await odd.stop()


async def test_a_connect_cancelled_while_mapd_is_asked_leaves_no_link_open(
    robotd: FakeRobotd, mapd: FakeMapd
) -> None:
    mapd.slow_s = 10.0  # the stream answers at once, map.grid takes its time
    t = JsonRpcUnixTransport(
        f"tcp://127.0.0.1:{robotd.port}", map_address=mapd.address, request_timeout_s=5.0
    )
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(t.connect(), timeout=3.0)
    await t.close()
    assert not _links(), "mapd's links outlived a cancelled connect"


def test_a_state_with_no_clock_is_not_believed() -> None:
    source, track = _source()
    _odometry(source, track, 0.0, 0.0, 0.0, 10.0)
    source.took({"t_ns": 0, "pose": {"x": 1.0, "y": 0.0, "yaw": 0.0}, "keyframes": 3})
    assert "no clock" in (source.fix().lost or "")


class _HangsUpAfterTheStream(FakeMapd):
    """Answers map.stream, then drops that link a moment later, as a mapd that dies does."""

    async def _answer(self, msg: dict[str, Any], writer: asyncio.StreamWriter) -> None:
        await super()._answer(msg, writer)
        if msg.get("method") == mapd_api.MAPD_STREAM.name and not getattr(self, "done", False):
            self.done = True
            await writer.drain()
            await asyncio.sleep(0.05)
            writer.close()


async def test_a_link_lost_while_the_other_opens_is_tried_again(monkeypatch: Any) -> None:
    fake = _HangsUpAfterTheStream()
    await fake.start()
    real = asyncio.open_connection
    calls = {"n": 0}

    async def slow_second(*a: Any, **k: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 2:  # the asking link, through a slow tunnel
            await asyncio.sleep(0.3)
        return await real(*a, **k)

    monkeypatch.setattr(asyncio, "open_connection", slow_second)
    source = MapdSource(fake.address, track=None, request_timeout_s=1.0)
    try:
        with contextlib.suppress(Exception):
            await source.start()
        await asyncio.sleep(0.1)
        if source.fix().lost is None:
            await asyncio.sleep(3 * STALE_S)
        assert fake.methods().count(mapd_api.MAPD_STREAM.name) > 1 or source.client._stream is None
    finally:
        await source.close()
        await fake.stop()


def test_a_map_address_is_refused_for_a_body_that_reads_no_map(tmp_path: Any) -> None:
    from quackd.registry import Registry, RegistryError, RobotEntry

    reg = Registry(tmp_path)
    with pytest.raises(RegistryError, match="reads no map the robot keeps"):
        reg.add_robot(RobotEntry(name="arm", spec="lerobot:mock", map_address="tcp://h:9871"))
    reg.add_robot(RobotEntry(name="duck", spec="microduck:jsonrpc", map_address="tcp://h:9871"))
    with pytest.raises(RegistryError, match="--clear map-address"):
        reg.update_robot("duck", {"spec": "microduck:sim2d"})
    reg.update_robot("duck", {"spec": "microduck:sim2d", "map_address": None})


def test_doctor_refuses_a_map_address_with_no_robot_to_connect_to() -> None:
    from typer.testing import CliRunner

    from quackd.cli import app

    result = CliRunner().invoke(
        app, ["doctor", "--robot", "microduck:jsonrpc", "--map-address", "tcp://127.0.0.1:1"]
    )
    assert result.exit_code != 0
    assert "--map-address needs a robot doctor connects to" in result.output
