"""Two robots meeting end to end on a fake air: adverts heard, near decided, the lower beacon id
dialling, a hello over a link or the LAN, and a bye when one walks away.

Everything below talk is the real code: the tracker, the planner, the meeter, links, transports,
on a `FakeAir` whose clock the test turns and a loop thread as the service has. In most tests talk
itself is a stand-in (`StandInTalker`), a hello of one line each way, because what is tested is
that the right robot is said hello to over the right way at the right time; the last runs the
real Talker with owner-signed passports through the same `TalkerPort`. `Robot` is the wiring a
robot's service does."""

from __future__ import annotations

import json
import random
import threading
import time
from collections.abc import Callable
from typing import Any

import pytest

from quackd.lan.lookup import LanRecord
from quackd.passport.beacon import ble_service_data, new_beacon_id
from quackd.passport.errors import PassportError
from quackd.passport.fetch import HttpStatus, Unreachable
from quackd.radio import Pipe, Sighting
from quackd.radio.fake import FakeAir, FakeLan
from quackd.radio.links import LinkChange, LinkTable, LinkTransport, talk_inbox
from quackd.radio.loop import LoopThread
from quackd.radio.meet import INBOX_PATH, Meeter, MeetEvent, MeetPolicy, Planner, TalkerPort
from quackd.radio.proximity import ProximityPolicy, Tracker
from quackd.radio.sim import AirModel
from quackd.radio.transport import Transports

QUIET_AIR = AirModel(noise_db=0.0, loss=0.0)


class StandInTalker:
    """Talk reduced to what meeting needs: a hello names the robot that sent it and the beacon id
    it advertises, the answer names the other, a bye ends it. A robot can be told to refuse."""

    def __init__(self, did: str, beacon: Callable[[], str], transports: Transports) -> None:
        self.did, self.beacon, self.transports = did, beacon, transports
        self.sessions: dict[str, str] = {}
        self.beacons: dict[str, str] = {}
        self.left: list[str] = []
        self.answer_with: int = 200
        self.on_session: Callable[[str, bool, str, str], None] | None = None
        self._lock = threading.Lock()

    def meet(self, route: str, *, expect_beacon: str | None = None) -> str:
        status, reply = self.transports.post(
            route, json.dumps({"hello": self.did, "bt": self.beacon()})
        )
        if status != 200 or reply is None:
            raise HttpStatus(route, status, "")
        answer = json.loads(reply)
        if expect_beacon is not None and answer["bt"] != expect_beacon:
            raise PassportError("6.3", "the robot that answered advertises another beacon id")
        with self._lock:
            self.sessions[answer["did"]] = route
            self.beacons[answer["did"]] = answer["bt"]
        if self.on_session:
            self.on_session(answer["bt"], True, route, answer["did"])
        return str(answer["did"])

    def inbox(self, wire: str, origin: str) -> tuple[int, str | None]:
        doc = json.loads(wire)
        if "bye" in doc:
            with self._lock:
                self.sessions.pop(doc["bye"], None)
            if self.on_session:
                self.on_session(doc.get("bt", ""), False, origin, doc["bye"])
            return 204, None
        if self.answer_with != 200:
            return self.answer_with, "not you, not now"
        with self._lock:
            self.sessions[doc["hello"]] = origin
            self.beacons[doc["hello"]] = doc["bt"]
        if self.on_session:
            self.on_session(doc["bt"], True, origin, doc["hello"])
        return 200, json.dumps({"did": self.did, "bt": self.beacon()})

    def leave(self, peer_did: str) -> None:
        with self._lock:
            route = self.sessions.pop(peer_did, None)
            self.left.append(peer_did)
        if route is not None:
            self.transports.post(route, json.dumps({"bye": self.did, "bt": self.beacon()}))
        if self.on_session:
            self.on_session(self.beacons.get(peer_did, ""), False, route or "", peer_did)

    def peers_open(self) -> set[str]:
        with self._lock:
            return set(self.sessions)


class Network:
    """The LANs in the test: which robot answers at which address, and on which network."""

    def __init__(self) -> None:
        self.hosts: dict[tuple[str, int], tuple[str, StandInTalker]] = {}

    def http_for(
        self, network: str | None, me: str
    ) -> Callable[[str, str], tuple[int, str | None]]:
        def post(route: str, wire: str) -> tuple[int, str | None]:
            host, _, rest = route.removeprefix("http://").partition(":")
            port = int(rest.split("/")[0])
            found = self.hosts.get((host, port))
            if network is None or found is None or found[0] != network:
                raise Unreachable(f"{route} could not be reached")
            return found[1].inbox(wire, f"http://{me}")

        return post


class Robot:
    """One robot's radio half, wired as its service wires it."""

    def __init__(
        self,
        air: FakeAir,
        name: str,
        at: tuple[float, float],
        loop: LoopThread,
        net: Network,
        *,
        beacon: str,
        network: str | None = None,
        lan: FakeLan | None = None,
        ip: str = "10.0.0.1",
        meet_policy: MeetPolicy | None = None,
    ) -> None:
        self.air, self.loop, self.beacon, self.ip = air, loop, beacon, ip
        self.did = f"did:key:z6Mk{name}"
        self.radio = air.radio(name, at)
        self.tracker = Tracker(ProximityPolicy(), me=beacon)
        self.planner = Planner(
            lambda: self.beacon, meet_policy or MeetPolicy(), rng=random.Random(0)
        )
        self.links = LinkTable(on_change=self._link_changed)
        self.transports = Transports(
            http=net.http_for(network, ip), link=LinkTransport(self.links, loop)
        )
        self.talker = StandInTalker(self.did, lambda: self.beacon, self.transports)
        self.talker.on_session = self._session
        self.events: list[MeetEvent] = []
        self.meeter = Meeter(
            planner=self.planner,
            talker=self.talker,
            links=self.links,
            radio=self.radio,
            loop=loop,
            me=lambda: self.beacon,
            inbox_for=lambda route: talk_inbox(self.talker.inbox, route),
            lan=lan,
            clock=lambda: air.clock,
            on_event=self.events.append,
        )
        self.lan_flag = network is not None
        if network is not None:
            net.hosts[(ip, 8790)] = (network, self.talker)
            if lan is not None:
                lan.publish(LanRecord(ip, 8790, name, beacon))
        self._accepting: set[Any] = set()

    async def start(self) -> None:
        await self.radio.start(
            advert=ble_service_data(self.beacon, lan=self.lan_flag, link=True),
            on_sighting=self._heard,
            on_link=self._linked,
        )

    def _heard(self, sighting: Sighting) -> None:
        for event in self.tracker.observe(sighting):
            self.planner.on_proximity(event)

    def _linked(self, pipe: Pipe, address: str) -> None:
        task = self.loop.loop.create_task(
            self.links.accept(
                pipe, me=self.beacon, inbox_for=lambda route: talk_inbox(self.talker.inbox, route)
            )
        )
        self._accepting.add(task)
        task.add_done_callback(self._accepting.discard)

    def _link_changed(self, change: LinkChange) -> None:
        now = self.air.clock
        if change.opened:
            self.tracker.hold(change.beacon_id, now)
        else:
            lost = change.reason.startswith("lost")
            for event in self.tracker.release(change.beacon_id, now, lost=lost):
                self.planner.on_proximity(event)
        self.planner.on_link(
            change.beacon_id, opened=change.opened, inbound=change.inbound, now=now
        )

    def _session(self, beacon: str, opened: bool, route: str, did: str) -> None:
        self.planner.on_session(
            beacon or None, opened=opened, route=route, did=did, now=self.air.clock
        )

    def tick(self) -> None:
        now = self.air.clock
        for event in self.loop.call(_tick(self.tracker, now), 5):
            self.planner.on_proximity(event)
        self.meeter.step(now)

    def state_of(self, beacon: str) -> str | None:
        return next((p.state for p in self.planner.peers() if p.beacon_id == beacon), None)


async def _tick(tracker: Tracker, now: float) -> list[Any]:
    return tracker.tick(now)


@pytest.fixture
def loop() -> Any:
    thread = LoopThread("test-meeting").start()
    yield thread
    thread.stop()


def pair() -> tuple[str, str]:
    low, high = sorted(new_beacon_id() for _ in range(2))
    return low, high


def run(
    air: FakeAir, loop: LoopThread, robots: list[Robot], done: Callable[[], bool], *, fake_s: float
) -> None:
    """Turn the air's clock 0.2 s at a time, ticking every robot, until `done` or `fake_s`."""
    deadline = time.monotonic() + 30.0
    end = air.clock + fake_s
    while not done():
        if air.clock >= end or time.monotonic() > deadline:
            raise AssertionError(f"not done after {fake_s:g} s of air")
        loop.call(air.step(0.2), 5)
        for robot in robots:
            robot.tick()
        time.sleep(0.01)  # the meeter's workers run in real time


def teardown(loop: LoopThread, robots: list[Robot]) -> None:
    for robot in robots:
        robot.meeter.shutdown()
        loop.call(robot.links.close_all(), 5)
        robot.links.shutdown()


def test_two_strangers_with_no_shared_network_meet_over_a_link_and_part(loop: LoopThread) -> None:
    air = FakeAir(QUIET_AIR)
    net = Network()
    low, high = pair()
    a = Robot(air, "a", (0.0, 0.0), loop, net, beacon=low)
    b = Robot(air, "b", (1.0, 0.0), loop, net, beacon=high)
    robots = [a, b]
    for robot in robots:
        loop.call(robot.start(), 5)
    try:
        run(
            air,
            loop,
            robots,
            lambda: a.state_of(high) == "met" and b.state_of(low) == "met",
            fake_s=10,
        )
        assert a.talker.sessions[b.did].startswith("ble://")
        assert b.talker.sessions[a.did].startswith("ble://")
        met = [e for e in a.events if e.kind == "met"]
        assert [(e.via, e.did) for e in met] == [("link", b.did)]
        # the higher robot never dialled: the lower did, and its link kept the other waiting
        assert not [e for e in b.events if e.kind == "meeting"]
        # b walks away, still heard but quiet: far after the hold, a bye, and the link closed
        air.move("b", (30.0, 0.0))
        policy = ProximityPolicy()
        run(
            air,
            loop,
            robots,
            lambda: b.did in a.talker.left,
            fake_s=policy.window_s + policy.far_hold_s + 5,
        )
        run(air, loop, robots, lambda: len(a.links) == 0 and len(b.links) == 0, fake_s=5)
        assert a.did not in b.talker.peers_open()
        assert [e.kind for e in a.events][-1] == "parted"
    finally:
        teardown(loop, robots)


def test_robots_that_stay_near_meet_again_over_the_link_they_already_hold(
    loop: LoopThread,
) -> None:
    """A meeting ends and the two robots stay where they are: after the re-meet time they meet
    again, over the link that carried the first meeting, and neither ends up holding two."""
    air = FakeAir(QUIET_AIR)
    net = Network()
    low, high = pair()
    policy = MeetPolicy(remeet_s=3.0)
    a = Robot(air, "a", (0.0, 0.0), loop, net, beacon=low, meet_policy=policy)
    b = Robot(air, "b", (1.0, 0.0), loop, net, beacon=high, meet_policy=policy)
    robots = [a, b]
    for robot in robots:
        loop.call(robot.start(), 5)

    def met() -> int:
        return sum(1 for e in a.events if e.kind == "met")

    try:
        run(air, loop, robots, lambda: met() == 1, fake_s=10)
        first = a.talker.sessions[b.did]
        a.talker.leave(b.did)  # the meeting is over, and both stay put
        run(air, loop, robots, lambda: met() == 2, fake_s=policy.remeet_s + 5)
        assert a.talker.sessions[b.did] == first
        assert len(a.links) == 1 and len(b.links) == 1
    finally:
        teardown(loop, robots)


def test_two_robots_on_one_network_meet_over_the_lan_and_hold_no_link(loop: LoopThread) -> None:
    air = FakeAir(QUIET_AIR)
    net, lan = Network(), FakeLan()
    low, high = pair()
    a = Robot(air, "a", (0.0, 0.0), loop, net, beacon=low, network="office", lan=lan, ip="10.0.0.1")
    b = Robot(
        air, "b", (1.0, 0.0), loop, net, beacon=high, network="office", lan=lan, ip="10.0.0.2"
    )
    robots = [a, b]
    for robot in robots:
        loop.call(robot.start(), 5)
    try:
        run(air, loop, robots, lambda: a.state_of(high) == "met", fake_s=10)
        met = next(e for e in a.events if e.kind == "met")
        assert met.via == "lan" and met.route == f"http://10.0.0.2:8790{INBOX_PATH}"
        assert b.talker.sessions[a.did] == "http://10.0.0.1"
        assert len(a.links) == 0 and len(b.links) == 0
    finally:
        teardown(loop, robots)


def test_a_lan_record_that_does_not_answer_falls_back_to_a_link(loop: LoopThread) -> None:
    air = FakeAir(QUIET_AIR)
    net, lan = Network(), FakeLan()
    low, high = pair()
    # each is on a network, so each advertises a LAN record, but not on the same one
    a = Robot(air, "a", (0.0, 0.0), loop, net, beacon=low, network="home", lan=lan, ip="10.0.0.1")
    b = Robot(
        air, "b", (1.0, 0.0), loop, net, beacon=high, network="office", lan=lan, ip="10.0.1.2"
    )
    robots = [a, b]
    for robot in robots:
        loop.call(robot.start(), 5)
    try:
        run(air, loop, robots, lambda: a.state_of(high) == "met", fake_s=10)
        met = next(e for e in a.events if e.kind == "met")
        assert met.via == "link"
        assert len(a.links) == 1
    finally:
        teardown(loop, robots)


@pytest.mark.parametrize(("answer", "state"), [(400, "refused"), (429, "waiting")])
def test_a_refusal_holds_a_robot_back_for_an_hour_and_busy_for_a_moment(
    loop: LoopThread, answer: int, state: str
) -> None:
    air = FakeAir(QUIET_AIR)
    net = Network()
    low, high = pair()
    a = Robot(air, "a", (0.0, 0.0), loop, net, beacon=low)
    b = Robot(air, "b", (1.0, 0.0), loop, net, beacon=high)
    b.talker.answer_with = answer
    robots = [a, b]
    for robot in robots:
        loop.call(robot.start(), 5)
    try:
        run(
            air,
            loop,
            robots,
            lambda: any(e.kind in ("refused", "busy") for e in a.events),
            fake_s=10,
        )
        assert a.state_of(high) == state
        plan = next(p for p in a.planner.peers() if p.beacon_id == high)
        wait = MeetPolicy().refused_s if answer == 400 else MeetPolicy().busy_s
        assert plan.next_at == pytest.approx(air.clock + wait, abs=1.0)
    finally:
        teardown(loop, robots)


def test_a_fake_radio_is_off_until_started_and_the_stand_in_is_a_talker(loop: LoopThread) -> None:
    air = FakeAir()
    talker: TalkerPort = StandInTalker(
        "did:key:z6MkX", lambda: "0" * 16, Transports(http=lambda r, w: (204, None))
    )
    assert talker.peers_open() == set()
    radio = air.radio("x")
    assert radio.status().state == "off"
    loop.call(
        radio.start(
            advert=ble_service_data("0" * 16, lan=False, link=True),
            on_sighting=print,
            on_link=print,
        ),
        5,
    )
    assert radio.status().ready
    loop.call(radio.stop(), 5)


# ── with talk itself: lane C's Talker, owner-signed passports, the smallest link there is ──


def test_two_robots_with_real_passports_meet_over_the_smallest_link_say_a_word_and_part(
    loop: LoopThread,
) -> None:
    """The whole of it but the radio's own hardware: two robots with 1.0 passports their owners
    signed, each robot's real Talker, a hello of a few kilobytes carried in 20-byte datagrams,
    both verifications done, a word said in the session, and a bye when one walks away."""
    from quackd.passport.link import MIN_DATAGRAM
    from quackd.passport_talk import Talker
    from quackd.passport_trust import Judgement
    from tests.passport_helpers import OWNER, OWNER_B, A, B, passport

    air = FakeAir(QUIET_AIR)
    low, high = pair()

    class Robot2:
        def __init__(self, name: str, at: tuple[float, float], key: Any, owner: Any, beacon: str):
            self.beacon = beacon
            self.events: list[MeetEvent] = []
            self.changes: list[LinkChange] = []
            self.radio = air.radio(name, at, datagram_size=MIN_DATAGRAM)
            self.tracker = Tracker(ProximityPolicy(), me=beacon)
            self.planner = Planner(lambda: self.beacon, MeetPolicy(), rng=random.Random(0))
            self.links = LinkTable(on_change=self._link_changed)
            self.transports = Transports(
                http=lambda r, w: (503, None), link=LinkTransport(self.links, loop)
            )
            self.talker = Talker(
                name,
                key,
                # padded so a hello is several kilobytes: hundreds of 20-byte datagrams
                passport(key, owner, name, notes="x" * 3000),
                judge=lambda reading: Judgement("stranger", "a test's", ()),
                send=self.transports.post,
                heard=lambda b: self.tracker.heard(b, within_s=8, now=air.clock),
                on_session=self._session,
                runtime={"software": "quackd/test", "beacon": beacon, "reach": {"http_port": 8790}},
            )
            self.meeter = Meeter(
                planner=self.planner,
                talker=self.talker,
                links=self.links,
                radio=self.radio,
                loop=loop,
                me=lambda: self.beacon,
                inbox_for=lambda route: talk_inbox(self.talker.inbox, route),
                clock=lambda: air.clock,
                on_event=self.events.append,
            )
            self.accepting: set[Any] = set()

        async def start(self) -> None:
            await self.radio.start(
                advert=ble_service_data(self.beacon, lan=False, link=True),
                on_sighting=lambda s: [
                    self.planner.on_proximity(e) for e in self.tracker.observe(s)
                ],
                on_link=self._linked,
            )

        def _linked(self, pipe: Pipe, address: str) -> None:
            task = loop.loop.create_task(
                self.links.accept(
                    pipe, me=self.beacon, inbox_for=lambda r: talk_inbox(self.talker.inbox, r)
                )
            )
            self.accepting.add(task)

        def _link_changed(self, change: LinkChange) -> None:
            self.changes.append(change)
            if change.opened:
                self.tracker.hold(change.beacon_id, air.clock)
            else:
                for e in self.tracker.release(
                    change.beacon_id, air.clock, lost=change.reason.startswith("lost")
                ):
                    self.planner.on_proximity(e)
            self.planner.on_link(
                change.beacon_id, opened=change.opened, inbound=change.inbound, now=air.clock
            )

        def _session(self, session: Any, opened: bool) -> None:
            beacon = session.runtime.get("beacon")
            self.planner.on_session(
                beacon if isinstance(beacon, str) else None,
                opened=opened,
                route=session.route,
                did=session.peer,
                now=air.clock,
            )

        def tick(self) -> None:
            for event in loop.call(_tick(self.tracker, air.clock), 5):
                self.planner.on_proximity(event)
            self.meeter.step(air.clock)

    a = Robot2("courier", (0.0, 0.0), A, OWNER, low)
    b = Robot2("household", (1.0, 0.0), B, OWNER_B, high)
    robots = [a, b]
    for robot in robots:
        loop.call(robot.start(), 5)

    def run2(done: Callable[[], bool], fake_s: float) -> None:
        deadline, end = time.monotonic() + 60.0, air.clock + fake_s
        while not done():
            if air.clock >= end or time.monotonic() > deadline:
                said = [(r.beacon[:4], e.kind, e.via, e.reason) for r in robots for e in r.events]
                plans = [
                    (r.beacon[:4], p.state, p.reason) for r in robots for p in r.planner.peers()
                ]
                raise AssertionError(f"not done after {fake_s:g} s of air: {said} {plans}")
            loop.call(air.step(0.2), 5)
            for robot in robots:
                robot.tick()
            time.sleep(0.01)

    try:
        # met once the meeter says so: the Talker that dialled lists the session as soon as the
        # second hello arrives, and has its route only once the third step has come back
        run2(
            lambda: any(e.kind == "met" for e in a.events) and a.talker.id in b.talker.peers_open(),
            20,
        )
        [link] = a.links.links()
        assert link.send_size == MIN_DATAGRAM
        # the link the courier opened is the household's to report once the courier's hello,
        # signed and naming the beacon id its OPEN claimed, has been accepted over it
        assert [(c.opened, c.inbound, c.beacon_id) for c in b.changes] == [(True, True, low)]
        # the two hellos with a passport in each, a few kilobytes cut into 20-byte datagrams
        assert link.pipe.sent > 2 * 3000 // MIN_DATAGRAM  # type: ignore[attr-defined]
        # a word in the session, over the same link, read on the other side
        a.talker.say(b.talker.id, "parcel for the kitchen door")
        said = [h for h in b.talker.drain() if h.kind == "said"]
        assert said and said[-1].text == "parcel for the kitchen door"
        # the household robot walks off: far, a bye, the link closed, the sessions over
        air.move("household", (30.0, 0.0))
        policy = ProximityPolicy()
        run2(lambda: not a.talker.peers_open(), policy.window_s + policy.far_hold_s + 10)
        run2(lambda: len(a.links) == 0 and not b.talker.peers_open(), 10)
    finally:
        teardown(loop, robots)  # type: ignore[arg-type]
