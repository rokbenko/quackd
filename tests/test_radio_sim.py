"""The sim air in one process: the model, the scripted paths, the files radios leave on the air,
two radios hearing each other and talking over a loopback link, and the sim LAN.

Every number is the model's own (`AirModel`), or a path's; nothing here measures how long the
air takes beyond waiting, with a deadline, for what has to happen."""

from __future__ import annotations

import asyncio
import json
import random
import time
from pathlib import Path as FilePath

import pytest

from quackd.lan.lookup import LanRecord
from quackd.lan.passports import LanRecord as ExportedLanRecord
from quackd.passport.beacon import (
    FLAG_LAN,
    FLAG_LINK,
    advertising_data,
    ble_service_data,
    new_beacon_id,
)
from quackd.passport.link import Media
from quackd.radio import Pipe, Sighting, open_radio
from quackd.radio.links import LinkInbox, LinkTable
from quackd.radio.sim import (
    OP_WRITE_REQ,
    STALE_S,
    AirModel,
    Path,
    SimLan,
    SimOptions,
    SimRadio,
    read_radio,
    read_scene,
    write_scene,
)


def test_a_path_walks_in_straight_lines_and_stands_still_at_either_end() -> None:
    path = Path.parse("6,0@0 1,0@8 1,0@18 6,0@23")
    assert path.at(-5) == (6.0, 0.0)
    assert path.at(4) == pytest.approx((3.5, 0.0))
    assert path.at(10) == (1.0, 0.0)
    assert path.at(20.5) == pytest.approx((3.5, 0.0))
    assert path.at(100) == (6.0, 0.0)
    assert str(path) == "6,0@0 1,0@8 1,0@18 6,0@23"
    assert Path.parse("2,3").at(50) == (2.0, 3.0)
    for bad in ("", "1@0", "a,b@0", "1,0@5 2,0@4", "nan,0@0"):
        with pytest.raises(ValueError):
            Path.parse(bad)


def test_the_model_is_log_distance_with_bounded_noise_and_some_adverts_lost() -> None:
    model = AirModel()
    assert model.clean_rssi(1.0) == model.rssi_at_1m
    assert model.clean_rssi(10.0) == pytest.approx(model.rssi_at_1m - 10 * model.path_loss_n)
    assert model.clean_rssi(0.0) == model.clean_rssi(0.1)
    rng = random.Random(7)
    heard = [model.heard(2.0, rng) for _ in range(4000)]
    lost = heard.count(None) / len(heard)
    assert abs(lost - model.loss) < 0.02
    clean = model.clean_rssi(2.0)
    values = [h for h in heard if h is not None]
    assert all(abs(v - clean) <= model.noise_bound_db + 0.05 for v in values)
    assert abs(sum(values) / len(values) - clean) < 0.2
    # far enough away nothing is heard, however lucky the noise
    beyond = 10 ** ((model.rssi_at_1m - model.floor_dbm + model.noise_bound_db) / 20 / 1.1)
    assert not model.in_range(beyond * 2)
    assert all(model.heard(beyond * 2, rng) is None for _ in range(500))


def test_a_scene_is_shared_through_the_air(tmp_path: FilePath) -> None:
    assert read_scene(tmp_path) is None
    write_scene(tmp_path, t0=1000.0, speed=4.0)
    assert read_scene(tmp_path) == (1000.0, 4.0)
    (tmp_path / "scene.json").write_text('{"v":1,"t0":5,"speed":0}', encoding="utf-8")
    assert read_scene(tmp_path) is None


def radio_file(air: FilePath, token: str, **fields: object) -> None:
    doc: dict[str, object] = {
        "v": 1,
        "ad": advertising_data(ble_service_data(new_beacon_id(), lan=True, link=True)).hex(),
        "x": 1.0,
        "y": 0.0,
        "link": 40000,
        "mtu": 185,
        "at": time.time(),
    }
    doc.update(fields)
    (air / "radios").mkdir(parents=True, exist_ok=True)
    (air / "radios" / f"{token}.json").write_text(json.dumps(doc), encoding="utf-8")


def test_only_a_fresh_well_formed_passport_advert_is_read_off_the_air(tmp_path: FilePath) -> None:
    radio_file(tmp_path, "good")
    record = read_radio(tmp_path, "good")
    assert record is not None and record.beacon.flags == FLAG_LAN | FLAG_LINK
    radio_file(tmp_path, "stale", at=time.time() - STALE_S - 1)
    radio_file(tmp_path, "notours", ad="0201060303aafe")
    radio_file(tmp_path, "nolink", link=0)
    radio_file(tmp_path, "nowhere", x="far")
    (tmp_path / "radios" / "junk.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "radios" / "huge.json").write_text(" " * 5000 + "{}", encoding="utf-8")
    for token in ("stale", "notours", "nolink", "nowhere", "junk", "huge", "missing", "../good"):
        assert read_radio(tmp_path, token) is None, token
    # a tiny MTU is read as the smallest there is, and a huge one as the largest a link takes
    radio_file(tmp_path, "tiny", mtu=3)
    tiny = read_radio(tmp_path, "tiny")
    assert tiny is not None and tiny.mtu == 23


async def until(condition: object, within_s: float = 5.0) -> None:
    deadline = time.monotonic() + within_s
    while not (condition() if callable(condition) else condition):
        if time.monotonic() > deadline:
            raise AssertionError("the condition never came true")
        await asyncio.sleep(0.02)


class Robot:
    """A sim radio, what it heard, and a link table answering whoever dials it."""

    def __init__(self, air: FilePath, path: str, *, beacon: str | None = None, **opts: object):
        self.beacon = beacon or new_beacon_id()
        self.radio = SimRadio(SimOptions(air=air, path=Path.parse(path), **opts))  # type: ignore[arg-type]
        self.heard: list[Sighting] = []
        self.links: list[Pipe] = []
        self.table = LinkTable()
        self.tasks: set[asyncio.Task[object]] = set()

    def answer(self, body: bytes, media: Media) -> tuple[int, bytes | None, Media]:
        return 200, b"from " + self.beacon.encode() + b": " + body, Media.TEXT

    def inbox_for(self, route: str) -> LinkInbox:
        return self.answer

    async def start(self, *, lan: bool = False) -> None:
        def on_link(pipe: Pipe, address: str) -> None:
            self.links.append(pipe)
            task = asyncio.get_running_loop().create_task(
                self.table.accept(pipe, me=self.beacon, inbox_for=self.inbox_for)
            )
            self.tasks.add(task)

        status = await self.radio.start(
            advert=ble_service_data(self.beacon, lan=lan, link=True),
            on_sighting=self.heard.append,
            on_link=on_link,
        )
        assert status.ready and status.advertising and status.scanning and status.accepting

    async def stop(self) -> None:
        await self.table.close_all()
        self.table.shutdown()
        await self.radio.stop()


async def test_two_radios_hear_each_other_at_the_strength_their_distance_gives(
    tmp_path: FilePath,
) -> None:
    t0 = time.time()
    a = Robot(tmp_path, "0,0@0", t0=t0)
    b = Robot(tmp_path, "2,0@0", t0=t0)
    await a.start(lan=True)
    await b.start()
    try:
        await until(lambda: a.heard and b.heard)
        model = AirModel()
        for robot, other in ((a, b), (b, a)):
            seen = robot.heard[0]
            assert seen.beacon_id == other.beacon and seen.address == other.radio.address
            assert seen.true_m == 2.0
            assert abs(seen.rssi - model.clean_rssi(2.0)) <= model.noise_bound_db
            assert {s.beacon_id for s in robot.heard} == {other.beacon}
        assert b.heard[0].flags == FLAG_LAN | FLAG_LINK and a.heard[0].flags == FLAG_LINK
    finally:
        await a.stop()
        await b.stop()
    assert list((tmp_path / "radios").glob("*.json")) == []


async def test_a_request_crosses_a_sim_link_and_comes_back(tmp_path: FilePath) -> None:
    t0 = time.time()
    a = Robot(tmp_path, "0,0@0", t0=t0, mtu=100)
    b = Robot(tmp_path, "1,0@0", t0=t0)
    await a.start()
    await b.start()
    try:
        await until(lambda: a.heard)
        link = await a.table.dial(
            a.radio,
            a.heard[0].address,
            me=a.beacon,
            expect=b.beacon,
            inbox_for=a.inbox_for,
        )
        # the two MTUs meet at the smaller, less the three bytes of a write's header
        assert link.send_size == 100 - 3
        status, reply, _ = await link.request(b"hello" * 600, Media.JOSE, timeout=10)
        assert status == 200 and reply == b"from " + b.beacon.encode() + b": " + b"hello" * 600
        await until(lambda: len(b.table) == 1)
        back = b.table.links()[0]
        status, reply, _ = await back.request(b"and you?", Media.JOSE, timeout=5)
        assert (status, reply) == (200, b"from " + a.beacon.encode() + b": and you?")
        assert a.radio.status().links == 1
    finally:
        await a.stop()
        await b.stop()


async def test_a_link_drops_once_its_robots_stay_out_of_range(tmp_path: FilePath) -> None:
    t0 = time.time()
    model = AirModel(supervision_s=0.4)
    a = Robot(tmp_path, "0,0@0", t0=t0, model=model)
    # walks out of range a second into the scene, and never comes back
    b = Robot(tmp_path, "1,0@0 1,0@1 5000,0@1.5", t0=t0, model=model)
    await a.start()
    await b.start()
    try:
        await until(lambda: a.heard)
        link = await a.table.dial(
            a.radio, a.heard[0].address, me=a.beacon, expect=b.beacon, inbox_for=a.inbox_for
        )
        await until(lambda: link.closed is not None, within_s=10.0)
        assert "out of range" in (link.closed or "") or "lost" in (link.closed or "")
        # and once it is out of range it cannot be dialled at all
        with pytest.raises(OSError, match="out of radio range"):
            await a.radio.connect(b.radio.address, timeout=2)
    finally:
        await a.stop()
        await b.stop()


async def test_a_sim_link_opens_by_subscribing_and_keeps_to_its_mtu(tmp_path: FilePath) -> None:
    t0 = time.time()
    b = Robot(tmp_path, "1,0@0", t0=t0)
    await b.start()
    try:
        record = read_radio(tmp_path, b.radio.token)
        assert record is not None
        # a write before subscribing is not how a passport link opens: hung up on
        reader, writer = await asyncio.open_connection("127.0.0.1", record.link)
        writer.write(bytes((OP_WRITE_REQ, 0, 1, 0x11)))
        await writer.drain()
        assert await asyncio.wait_for(reader.read(), 5) == b""
        writer.close()
        # a write longer than the MTU agreed is a link the peripheral drops
        a = SimRadio(SimOptions(air=tmp_path, t0=t0, mtu=50))
        await a.start(advert=bytes(10), on_sighting=lambda s: None, on_link=lambda p, a: None)
        pipe = await a.connect(b.radio.address, timeout=5)
        assert pipe.datagram_size == 47
        with pytest.raises(ValueError, match="over this link"):
            await pipe.send(bytes(48))
        await pipe.close()
        await a.stop()
    finally:
        await b.stop()


async def test_a_new_advert_waits_for_the_rate_limit_then_goes_on_the_air(
    tmp_path: FilePath,
) -> None:
    first, second = new_beacon_id(), new_beacon_id()
    fast = SimRadio(SimOptions(air=tmp_path, t0=time.time(), speed=100.0))
    await fast.start(
        advert=ble_service_data(first, lan=False, link=True),
        on_sighting=lambda s: None,
        on_link=lambda p, a: None,
    )
    try:
        await fast.set_advert(ble_service_data(second, lan=False, link=True))

        def on_air() -> str | None:
            record = read_radio(tmp_path, fast.token)
            return record.beacon.beacon_id if record else None

        await until(lambda: on_air() == second, within_s=5.0)
    finally:
        await fast.stop()
    slow = SimRadio(SimOptions(air=tmp_path, t0=time.time()))
    await slow.start(
        advert=ble_service_data(first, lan=False, link=True),
        on_sighting=lambda s: None,
        on_link=lambda p, a: None,
    )
    try:
        await slow.set_advert(ble_service_data(second, lan=False, link=True))
        await asyncio.sleep(0.6)
        record = read_radio(tmp_path, slow.token)
        assert record is not None and record.beacon.beacon_id == first
    finally:
        await slow.stop()


async def test_a_radio_too_far_away_or_gone_is_not_heard(tmp_path: FilePath) -> None:
    t0 = time.time()
    a = Robot(tmp_path, "0,0@0", t0=t0)
    far = Robot(tmp_path, "10000,0@0", t0=t0)
    radio_file(tmp_path, "ghost", at=time.time() - STALE_S - 1, x=0.5)
    await a.start()
    await far.start()
    try:
        await asyncio.sleep(1.0)
        assert a.heard == [] and far.heard == []
    finally:
        await a.stop()
        await far.stop()


async def test_open_radio_builds_a_sim_radio_from_options_or_keywords(tmp_path: FilePath) -> None:
    by_options = open_radio("sim", options=SimOptions(air=tmp_path, token="robot-a"))
    by_keywords = open_radio("sim", air=tmp_path, token="robot-b", mtu=60)
    assert isinstance(by_options, SimRadio) and by_options.token == "robot-a"
    assert isinstance(by_keywords, SimRadio) and by_keywords.options.mtu == 60
    with pytest.raises(ValueError, match="MTU"):
        open_radio("sim", air=tmp_path, mtu=10)
    with pytest.raises(ValueError, match="name"):
        open_radio("sim", air=tmp_path, token="Not/A/Token")


def test_the_sim_lan_finds_a_robot_by_the_beacon_id_it_advertises(tmp_path: FilePath) -> None:
    assert LanRecord is ExportedLanRecord
    beacon = new_beacon_id()
    home, office, nowhere = (
        SimLan(tmp_path, "home"),
        SimLan(tmp_path, "office"),
        SimLan(tmp_path, "none"),
    )
    txt = {"v": "2", "id": "0123456789abcdef", "bt": beacon}
    assert home.publish(txt, host="127.0.0.1", port=8790)
    assert home.lookup(beacon) == LanRecord("127.0.0.1", 8790, "0123456789abcdef", beacon)
    assert office.lookup(beacon) is None
    assert not nowhere.publish(txt, host="127.0.0.1", port=8790)
    assert nowhere.lookup(beacon) is None
    # a rotation re-announces under the same short id, with the new beacon id
    rotated = new_beacon_id()
    assert home.publish({**txt, "bt": rotated}, host="127.0.0.1", port=8790)
    assert home.lookup(beacon) is None and home.lookup(rotated) is not None
    (home.directory / "broken.json").write_text("[]", encoding="utf-8")
    (home.directory / "wrongport.json").write_text(
        json.dumps({"v": 1, "txt": {"bt": beacon}, "host": "127.0.0.1", "port": 0}),
        encoding="utf-8",
    )
    assert home.lookup(beacon) is None
    assert home.lookup("not a beacon id") is None
    home.withdraw()
    assert home.lookup(rotated) is None
    with pytest.raises(ValueError):
        home.publish({"id": "../escape"}, host="127.0.0.1", port=1)
    with pytest.raises(ValueError):
        SimLan(tmp_path, "../elsewhere")


async def test_a_radio_that_hangs_up_before_the_link_opens_is_a_connection_error(
    tmp_path: FilePath,
) -> None:
    """What `LinkTable.dial` is told when a robot cannot be had is a ConnectionError, whatever
    way the other radio said no: one at its limit simply hangs up."""

    async def hang_up(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.read(64)  # hears the subscribe out, then hangs up without an answer
        writer.close()

    server = await asyncio.start_server(hang_up, "127.0.0.1", 0)
    port = int(server.sockets[0].getsockname()[1])
    radio_file(tmp_path, "rude", link=port, x=0.5)
    me = SimRadio(SimOptions(air=tmp_path, t0=time.time()))
    await me.start(advert=bytes(10), on_sighting=lambda s: None, on_link=lambda p, a: None)
    try:
        with pytest.raises(ConnectionError, match="hung up"):
            await me.connect("sim:rude", timeout=5)
    finally:
        await me.stop()
        server.close()
        await server.wait_closed()
