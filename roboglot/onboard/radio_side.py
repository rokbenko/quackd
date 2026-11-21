"""The onboard service's radio half: which robots are near, meeting them, and the beacon id.

The parts are `roboglot.radio`'s, wired the way one robot's service needs them:

- every advert the radio hears goes to the tracker, which says when a robot came near and when
  it went far;
- the planner decides whom to say hello to and when, the lower beacon id first, and the meeter
  says it, over the LAN when the other robot's record answers and over a Bluetooth link when not;
- every session the Talker opens or ends, whichever robot said hello, goes back to the planner,
  and a link that is open holds its robot near;
- the beacon id rotates every quarter of an hour while nothing is open, in the advert, the LAN
  record and the hello at once.

Everything on the radio runs on one event loop thread (`LoopThread`); the service's tick, the
passport server's threads and the meeter's workers reach it through `call`.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import dataclasses
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol

from roboglot.lan.lookup import LanDirectory, LanRecord
from roboglot.onboard.brain import CallPool
from roboglot.passport.beacon import WELL_KNOWN_JSON, ble_service_data, txt_record
from roboglot.passport.talk import MAX_SESSIONS, Session
from roboglot.passport_talk import Talker
from roboglot.radio import NoRadio, Pipe, Radio, RadioStatus, RadioUnavailable, Sighting, open_radio
from roboglot.radio.links import (
    LinkChange,
    LinkInbox,
    LinkPolicy,
    LinkTable,
    LinkTransport,
    talk_inbox,
)
from roboglot.radio.loop import LoopThread
from roboglot.radio.meet import Meeter, MeetEvent, MeetPolicy, Planner
from roboglot.radio.proximity import ProximityEvent, ProximityPolicy, Tracker
from roboglot.radio.rotation import ROTATE_S, BeaconRotor
from roboglot.radio.statusview import Rate, radio_snapshot
from roboglot.radio.transport import Transports, http_post

#: A hello over the network is answered only from a robot whose advert the radio heard this
#: recently, or that holds a link to this one: the radio is what says near.
HEARD_WITHIN_S = 8.0
#: How long the radio may take to start, and any one call onto its loop.
START_S = 20.0
CALL_S = 5.0
#: What the journal says of a radio whose start the service's close cut short: the start is
#: cancelled where it waits, and the radio's stop then takes down what it had brought up.
CUT_SHORT = "radio {kind}: its start was cut short, as the service stops"
#: How many hellos the meeter says at once: its own default (`Meeter.executor`), kept.
MEET_WORKERS = 4


class Lan(Protocol):
    """Where this robot's LAN record goes, and where another robot's is found by its beacon id:
    DNS-SD on a robot's own computer, a directory on the sim board."""

    def publish(self, txt: Mapping[str, str], *, host: str, port: int) -> bool: ...

    def withdraw(self) -> None: ...

    def lookup(self, beacon_id: str) -> LanRecord | None: ...


class DnsSdLan:
    """The LAN of a robot's own computer: its record announced over DNS-SD
    (`_robot-passport._tcp`), again after each rotation, and other robots' records found by a
    browser when one is given. Without a browser, robots near are met over a link."""

    def __init__(
        self,
        *,
        instance: str,
        browser: LanDirectory | None = None,
        log: Callable[[str], None] = lambda _line: None,
    ) -> None:
        self.instance = instance
        self.browser = browser
        self.log = log
        self._announced: Any = None
        #: why the record was last not announced, said once until the reason changes
        self._why_not: str | None = None

    def publish(self, txt: Mapping[str, str], *, host: str, port: int) -> bool:
        from roboglot.lan.passports import announce_passport, passport_record

        self.withdraw()
        record = passport_record(
            instance=self.instance,
            port=port,
            short_id=txt["id"],
            digest=txt["sha"],
            beacon=txt.get("bt"),
            path=txt.get("path", WELL_KNOWN_JSON),
        )
        try:
            self._announced = announce_passport(record)
        except Exception as e:  # no zeroconf, no network yet, a name taken: the radio still meets
            why = f"{type(e).__name__}: {e}"
            if why != self._why_not:  # once a reason: it is tried again at every rotation
                self.log(f"radio: the LAN record was not announced: {why}")
            self._why_not = why
            return False
        self._why_not = None
        return True

    def passed_over(self) -> int:
        """How many other robots' LAN records the browse passed over for naming only an address
        no robot can be at; 0 with no browser."""
        count = getattr(self.browser, "passed_over", None)
        return int(count()) if callable(count) else 0

    def withdraw(self) -> None:
        # taken in one step: the service's close and the radio's may both withdraw it at once
        announced, self._announced = self._announced, None
        if announced is not None:
            with contextlib.suppress(Exception):
                announced.close()

    def lookup(self, beacon_id: str) -> LanRecord | None:
        return self.browser.lookup(beacon_id) if self.browser is not None else None

    def close(self) -> None:
        """The record withdrawn and the browse stopped, at the service's end."""
        self.withdraw()
        close = getattr(self.browser, "close", None)
        if close is not None:
            with contextlib.suppress(Exception):
                close()


class RadioSide:
    """One robot's radio, its LAN record and its meetings. `start` once the Talker exists, `tick`
    a few times a second, `close` at the end."""

    def __init__(
        self,
        radio: Radio,
        *,
        lan: Lan | None,
        server_host: str,
        record_host: str,
        policy: ProximityPolicy | None = None,
        meet_policy: MeetPolicy | None = None,
        rotate_s: float = ROTATE_S,
        log: Callable[[str], None] = lambda _line: None,
        clock: Callable[[], float] = time.monotonic,
        advertising: bool = True,
    ) -> None:
        self.radio = radio
        self.lan = lan
        #: where the passport server listens, and the address the LAN record names
        self.lan_host = server_host
        self.record_host = record_host
        self.log = log
        self.clock = clock
        #: whether something outside the radio holds this robot's attention, a meeting run;
        #: the service sets it, and the beacon id waits for it, for an hour at most
        self.busy_elsewhere: Callable[[], bool] = lambda: False
        self._busy_said = False
        #: what the meeter did, for the service's log and met.jsonl
        self.on_event: Callable[[MeetEvent], None] | None = None
        #: every proximity event, for a narration
        self.on_proximity: Callable[[ProximityEvent], None] | None = None
        self.loop = LoopThread("roboglot-radio")
        # the calls the radio, its meetings and its links run on threads of their own (BlueZ's
        # question to the board, a hello's post, a link's inbox) go to pools that count those
        # under way, so the service's close can say which would hold the process at its exit
        self._calls = (
            CallPool("roboglot-radio-call", "the radio's loop"),
            CallPool("meet", "the radio's meetings", max_workers=MEET_WORKERS),
            CallPool("passport-link", "the radio's links", max_workers=LinkPolicy().inflight),
        )
        self.loop.loop.set_default_executor(self._calls[0])
        self.rotor = BeaconRotor(
            period_s=rotate_s,
            busy=self._busy,
            on_rotate=self._rotated,
            on_overdue=self._held_too_long,
            clock=clock,
        )
        self.tracker = Tracker(policy or ProximityPolicy(), me=self.rotor.current)
        self.planner = Planner(
            lambda: self.rotor.current, meet_policy or MeetPolicy(), advertising=advertising
        )
        self.links = LinkTable(
            on_change=self._link_changed, on_gone=self._link_gone, executor=self._calls[2]
        )
        self.transports = Transports(http=http_post, link=LinkTransport(self.links, self.loop))
        self.talker: Talker | None = None
        self.meeter: Meeter | None = None
        self._txt: dict[str, str] | None = None
        self._port = 0
        self._on_lan = False
        self._accepting: set[asyncio.Task[Any]] = set()
        #: adverts heard so far, and the rate status shows of them
        self._sightings = 0
        self._rate = Rate()
        #: the radio's status as a start that did not answer in time left it, and the words
        #: that say so, which status shows until the radio's own status moves
        self._unanswered: tuple[RadioStatus, str] | None = None
        #: the radio's start while it runs, and whether `close` came: a close cuts a start under
        #: way short, and a start after a close never begins
        self._state = threading.Lock()
        self._starting: concurrent.futures.Future[RadioStatus] | None = None
        self._closed = False
        #: the LAN record withdrawn for good, as the service closes (`withdraw`)
        self._gone = False

    # ── what the Talker takes ───────────────────────────────────────────────────────────

    def send(self, route: str, wire: str) -> tuple[int, str | None]:
        """Talk on its way: over HTTP for an `http://` route, over the link for a `ble://` one."""
        return self.transports.post(route, wire)

    def heard(self, beacon: str, origin: str = "") -> bool:
        """Whether a hello over the network that names `beacon`, from `origin`, may be answered:
        the radio heard that beacon lately, and when the robot advertising it has a LAN record,
        the hello comes from an address that record names. A beacon id is public, in every
        advert and every record, so the radio's word alone let any host on the LAN borrow the
        id of a robot that really was near."""
        if not self.loop.running:
            return False
        try:
            if not self.loop.call(self._heard(beacon), CALL_S):
                return False
        except Exception:
            return False
        if self.lan is None or not origin.startswith("http://"):
            return True
        try:
            record = self.lan.lookup(beacon)
        except Exception:
            record = None
        if record is None:
            return True  # no record to hold it to: the radio's word stands
        address = origin.removeprefix("http://").strip("[]")
        return address in {record.host, *record.addresses}

    async def _heard(self, beacon: str) -> bool:
        return self.tracker.heard(beacon, within_s=HEARD_WITHIN_S, now=self.clock())

    def on_session(self, session: Session, opened: bool) -> None:
        """A session opened or ended, whichever robot said hello: the planner keeps count."""
        beacon = session.runtime.get("beacon")
        self.planner.on_session(
            beacon if isinstance(beacon, str) else None,
            opened=opened,
            route=session.route,
            did=session.peer,
            now=self.clock(),
        )

    # ── up and down ─────────────────────────────────────────────────────────────────────

    def start(self, talker: Talker, *, short_id: str, digest: str, http_port: int) -> RadioStatus:
        """The LAN record first, so the advert can say whether there is one, then the radio. A
        radio that cannot start says why in its status, and the LAN half goes on without it."""
        self.talker = talker
        talker.endpoint.runtime["beacon"] = self.rotor.current
        self.meeter = Meeter(
            planner=self.planner,
            talker=talker,
            links=self.links,
            radio=self.radio,
            loop=self.loop,
            me=lambda: self.rotor.current,
            inbox_for=self._inbox_for,
            lan=self.lan,
            clock=self.clock,
            on_event=self._meet_event,
            executor=self._calls[1],
        )
        self._txt = txt_record(short_id=short_id, digest=digest, beacon=self.rotor.current)
        self._port = http_port
        self._on_lan = self._publish(self._txt, http_port)
        with self._state:
            if self._closed or self._gone:  # closed before its start began: it never starts
                self.log(CUT_SHORT.format(kind=self.radio.kind))
                return self.radio.status()
            self.loop.start()
            starting = self._starting = self.loop.submit(
                self.radio.start(
                    advert=self._advert(), on_sighting=self._sighting, on_link=self._linked
                )
            )
        try:
            status = starting.result(START_S)
        except RadioUnavailable as e:
            self.radio = NoRadio.instead_of(e, kind=self.radio.kind)
            status = self.radio.status()
        except TimeoutError as e:
            # a radio that does not answer in time is a radio that is down, never the end of
            # the service: it is kept, and its own retries go on from where its start was cut,
            # as for a radio that came up down; until it moves, its status says what happened
            ours = not starting.done()
            if ours:  # cancelled where it waits, as `LoopThread.call` does past its bound
                starting.cancel()
            why = f"the radio did not answer within {START_S:g} s" if ours else str(e) or repr(e)
            self._unanswered = (self.radio.status(), why)
            status = self._as_said(self.radio.status())
        except concurrent.futures.CancelledError:
            # the service closed while the radio started: the start was cut short where it
            # waited, and the radio's stop follows in `close`
            self.log(CUT_SHORT.format(kind=self.radio.kind))
            return self.radio.status()
        finally:
            with self._state:
                self._starting = None
        if not status.ready:
            self.log(f"radio {status.kind}: {status.state}, {status.detail}")
        return status

    def tick(self) -> None:
        """What time did: robots near and far, meetings due, a rotation due."""
        if self.meeter is None or not self.loop.running:
            return
        now = self.clock()
        for event in self.loop.call(self._tick(now), CALL_S):
            self._proximity(event)
        self.meeter.step(now)
        self.rotor.tick(now)

    async def _tick(self, now: float) -> list[ProximityEvent]:
        if self._at_limit() != self._busy_said:
            with contextlib.suppress(Exception):  # no reason to stop hearing
                await self.radio.set_advert(self._advert())
        # silence from a radio that is not listening is not a robot gone
        if self.radio.status().hears:
            self.tracker.resume(now)
        else:
            self.tracker.suspend(now)
        return self.tracker.tick(now)

    def pools(self) -> tuple[CallPool, ...]:
        """The pools the radio's own calls run on, its loop's, its meetings' and its links'."""
        return self._calls

    def withdraw(self) -> None:
        """This robot's LAN record withdrawn, alone and at once, and for good: it is what other
        robots find this one by, and needs nothing of the radio, so the service's close does it
        first, and no start or rotation after it publishes the record again."""
        with self._state:
            self._gone = True
        if self.lan is not None:
            self.lan.withdraw()

    def _publish(self, txt: Mapping[str, str], port: int) -> bool:
        """The LAN record out, unless it has been withdrawn for good or the side has closed, and
        withdrawn again when that came while it went out: a publish is a blocking call, a second
        or so of zeroconf's probes, and a robot that has gone must not stay findable."""
        if self.lan is None:
            return False
        with self._state:
            if self._gone or self._closed:
                return False
        published = self.lan.publish(txt, host=self.record_host, port=port)
        with self._state:
            late = self._gone or self._closed
        if late:
            self.lan.withdraw()
            return False
        return published

    def close(self) -> None:
        """A start still under way cut short, then the meeter's workers, every link, the radio,
        the LAN record if it is still there, the loop."""
        with self._state:
            self._closed = True
            starting = self._starting
        if starting is not None:
            # first, so the radio's stop below never runs beside its own start: BlueZ's would
            # wait for the bring-up to let go, and be cut at its bound
            starting.cancel()
        if self.meeter is not None:
            self.meeter.shutdown()
        if self.loop.running:
            with contextlib.suppress(Exception):
                self.loop.call(self.links.close_all(), CALL_S)
            with contextlib.suppress(Exception):
                self.loop.call(self.radio.stop(), CALL_S)
        self.links.shutdown()
        if self.lan is not None:
            self.lan.withdraw()
            close = getattr(self.lan, "close", None)
            if close is not None:
                close()
        self.loop.stop()

    # ── on the radio's loop ─────────────────────────────────────────────────────────────

    def _advert(self) -> bytes:
        self._busy_said = self._at_limit()
        return ble_service_data(
            self.rotor.current, lan=self._on_lan, link=True, busy=self._busy_said
        )

    def _at_limit(self) -> bool:
        """At its link or session limit, which the advert says (spec 5.6), so that a robot heard
        does not dial only to be told so."""
        talker = self.talker
        return self.links.full or (
            talker is not None and len(talker.endpoint.sessions) >= MAX_SESSIONS
        )

    def _sighting(self, sighting: Sighting) -> None:
        self._sightings += 1
        for event in self.tracker.observe(sighting):
            self._proximity(event)

    def _proximity(self, event: ProximityEvent) -> None:
        self.planner.on_proximity(event)
        if event.kind != "seen":
            metres = f", about {event.metres:g} m" if event.metres is not None else ""
            self.log(f"radio: {event.beacon_id} is {event.kind}{metres} ({event.reason})")
        if self.on_proximity is not None:
            with contextlib.suppress(Exception):
                self.on_proximity(event)

    def _linked(self, pipe: Pipe, address: str) -> None:
        task = asyncio.get_running_loop().create_task(
            self.links.accept(pipe, me=self.rotor.current, inbox_for=self._inbox_for)
        )
        self._accepting.add(task)
        task.add_done_callback(self._accepting.discard)

    def _inbox_for(self, route: str) -> LinkInbox | None:
        talker = self.talker
        return talk_inbox(talker.inbox, route) if talker is not None else None

    def _link_gone(self, route: str) -> None:
        """The sessions a link carried end with it, off this loop, since telling a person of it may
        take a moment."""
        if self.talker is not None and self.meeter is not None:
            with contextlib.suppress(RuntimeError):  # a meeter already shut down
                self.meeter.executor.submit(self.talker.link_closed, route)

    def _link_changed(self, change: LinkChange) -> None:
        now = self.clock()
        how = "from" if change.inbound else "to"
        what = "opened" if change.opened else f"closed ({change.reason or 'done'})"
        self.log(f"radio: the link {how} {change.beacon_id} {what}")
        if change.opened:
            self.tracker.hold(change.beacon_id, now)
        else:
            lost = change.lost
            for event in self.tracker.release(change.beacon_id, now, lost=lost):
                self._proximity(event)
        self.planner.on_link(
            change.beacon_id, opened=change.opened, inbound=change.inbound, now=now
        )

    # ── rotation ────────────────────────────────────────────────────────────────────────

    def _busy(self) -> bool:
        talker = self.talker
        return (
            len(self.links) > 0
            or (talker is not None and bool(talker.peers_open()))
            or self.busy_elsewhere()
        )

    def _held_too_long(self) -> None:
        """Something has held the beacon id an hour past its time, which anybody can do by opening
        a link and keeping it talking: it ends, and only then does the id change."""
        if self.meeter is not None:
            self.meeter.part_from_everyone(then=self.rotor.rotate)

    def _rotated(self, fresh: str) -> None:
        """A new beacon id: the advert, the hello and the LAN record change together."""
        with contextlib.suppress(Exception):
            self.loop.call(self._advertise(fresh), CALL_S)
        if self.talker is not None:
            self.talker.endpoint.runtime["beacon"] = fresh
        if self.lan is not None and self._txt is not None:
            self._txt = {**self._txt, "bt": fresh}
            self._on_lan = self._publish(self._txt, self._port)
        self.log(f"radio: the beacon id is now {fresh}")

    async def _advertise(self, fresh: str) -> None:
        self.tracker.me = fresh
        await self.radio.set_advert(self._advert())

    # ── what it says ────────────────────────────────────────────────────────────────────

    def _meet_event(self, event: MeetEvent) -> None:
        via = f" over the {'LAN' if event.via == 'lan' else 'link'}" if event.via else ""
        why = f": {event.reason}" if event.reason and event.kind != "met" else ""
        self.log(f"radio: {event.kind} {event.beacon_id}{via}{why}")
        if self.on_event is not None:
            with contextlib.suppress(Exception):
                self.on_event(event)

    def status(self) -> dict[str, Any]:
        """The radio's part of status.json (`roboglot.radio.statusview`), taken on its loop, where
        the tracker lives, whether this robot has a LAN record and, on a LAN that browses, how
        many other robots' records were passed over for an address no robot can be at."""
        snapshot: dict[str, Any] | None = None
        if self.loop.running:
            with contextlib.suppress(Exception):
                snapshot = self.loop.call(self._snapshot(), CALL_S)
        if snapshot is None:
            snapshot = radio_snapshot(
                status=self._as_said(self.radio.status()), now=self.clock(), rotor=self.rotor
            )
        snapshot["on_lan"] = self._on_lan
        passed_over = getattr(self.lan, "passed_over", None)
        if callable(passed_over):
            snapshot["lan_passed_over"] = passed_over()
        return snapshot

    def _as_said(self, status: RadioStatus) -> RadioStatus:
        """`status`, naming the start that did not answer in time for as long as nothing of the
        radio has moved since: once it has, a retry under way or done, the radio's own words."""
        unanswered = self._unanswered
        if unanswered is None:
            return status
        if status != unanswered[0]:
            self._unanswered = None
            return status
        return dataclasses.replace(status, detail=unanswered[1])

    async def _snapshot(self) -> dict[str, Any]:
        now = self.clock()
        return radio_snapshot(
            status=self._as_said(self.radio.status()),
            now=now,
            rotor=self.rotor,
            tracker=self.tracker,
            planner=self.planner,
            heard_per_s=self._rate.update(self._sightings, now),
            main_conf=_main_conf() if self.radio.kind == "bluez" else None,
        )


def _main_conf() -> str | None:
    """BlueZ's main.conf, for the privacy line of a status: read, never written."""
    from roboglot.radio.bluez.privacy import MAIN_CONF

    try:
        return Path(MAIN_CONF).read_text(encoding="utf-8")
    except OSError:
        return None


def proximity_policy(
    *, meet_within_m: float, rssi_at_1m: float, speed: float = 1.0
) -> ProximityPolicy:
    """What near means for this robot, from robot.yaml; a sim scene run faster runs it faster."""
    policy = ProximityPolicy(meet_within_m=meet_within_m, rssi_at_1m=rssi_at_1m)
    return policy.scaled(speed) if speed != 1.0 else policy


def sim_side(
    *,
    air: Path,
    path: str,
    network: str,
    t0: float | None = None,
    speed: float | None = None,
    seed: int = 0,
    token: str | None = None,
    policy: ProximityPolicy | None = None,
    rotate_s: float = ROTATE_S,
    log: Callable[[str], None] = lambda _line: None,
) -> RadioSide:
    """The radio of the sim board: the sim air in `air`, walking `path`, on the sim LAN
    `network` (`none` for no LAN, so robots meet over the link). Its server listens on loopback,
    since every robot on the sim air is on this one computer."""
    from roboglot.radio.sim import Path as Walk
    from roboglot.radio.sim import SimLan, SimOptions

    air.mkdir(parents=True, exist_ok=True)
    options = SimOptions(
        air=air,
        path=Walk.parse(path),
        t0=t0,
        speed=speed,
        network=network,
        seed=seed,
        token=token,
    )
    return RadioSide(
        open_radio("sim", options=options),
        lan=SimLan(air, network),
        server_host="127.0.0.1",
        record_host="127.0.0.1",
        policy=policy,
        rotate_s=rotate_s,
        log=log,
    )


def board_side(
    *,
    radio_kind: str,
    instance: str,
    browser: LanDirectory | None = None,
    policy: ProximityPolicy | None = None,
    rotate_s: float = ROTATE_S,
    log: Callable[[str], None] = lambda _line: None,
    advertise: bool = True,
    find_adapter: Callable[[], str | None] | None = None,
) -> RadioSide:
    """The radio of a robot's own computer: BlueZ, or `off`, and DNS-SD on every interface. A
    radio it cannot have is a `NoRadio` saying why, and the robot meets others over its LAN.
    `browser` finds other robots' LAN records; without one, robots near are met over a link.
    `find_adapter` asks the board which adapter to open, the one preflight checks, each time the
    radio comes up, since one can appear or come back under another name while it runs."""
    options: dict[str, Any] = {"advertise": advertise} if radio_kind == "bluez" else {}
    if radio_kind == "bluez" and find_adapter is not None:
        options["find_adapter"] = find_adapter
    try:
        radio: Radio = open_radio(radio_kind, **options)  # type: ignore[arg-type]
    except RadioUnavailable as e:
        radio = NoRadio.instead_of(e, kind=radio_kind)
    return RadioSide(
        radio,
        lan=DnsSdLan(instance=instance, browser=browser, log=log),
        server_host="0.0.0.0",
        record_host="",
        policy=policy,
        rotate_s=rotate_s,
        log=log,
        advertising=advertise or radio_kind != "bluez",
    )


__all__ = [
    "CALL_S",
    "HEARD_WITHIN_S",
    "DnsSdLan",
    "Lan",
    "RadioSide",
    "board_side",
    "proximity_policy",
    "sim_side",
]
