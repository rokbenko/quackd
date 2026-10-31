"""Meeting: when a robot near is said hello to, by which robot, over which way, and when again.

Proximity says a robot is near; this decides what to do about it. The `Planner` is the pure half,
a table of the robots near with a state each and the clock passed in, and the `Meeter` carries
out what it decides, on worker threads, through the robot's Talker (`TalkerPort`, which talk
implements) so that a hello and the passport checks behind it run as they would over a LAN.

Two robots that hear each other would otherwise both dial, so the one with the lower beacon id
dials at once and the other gives it `grace_s` before it dials too; a robot that does not
advertise cannot be heard, so it always dials. A robot that could not be reached is tried again
after 2, 4, 8, 16, 32 and then every 60 seconds, each a little later at random so two robots that
failed together do not retry together; one that is busy after 30; one that refused, not for an
hour. What a robot remembers of another, a refusal or a meeting just ended, is kept by its `did`
once a hello has revealed it, and until then by the beacon id it was heard by, which a rotation
retires. Each try is numbered, and what a try that was given up on reports late is heard only if
it met the robot: nothing a try reports undoes a meeting already made.

The way is chosen per meeting. A robot whose advert says it has a LAN record, and whose record
this robot finds by the advert's beacon id, is met over HTTP, which is faster and leaves the
radio free; if that address does not answer, or there is no record, a robot that accepts links is
met over one. Either way the Talker checks everything that matters, so a LAN record that lies
about a beacon id gets nothing: the robot that answers has to sign a hello naming the beacon id
this robot heard.
"""

from __future__ import annotations

import contextlib
import itertools
import random
import threading
import time
from collections.abc import Callable
from concurrent.futures import Executor, ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from typing import Literal, Protocol

from quackd.lan.lookup import LanDirectory
from quackd.passport.beacon import FLAG_LAN, FLAG_LINK, initiates
from quackd.passport.errors import PassportError
from quackd.passport.fetch import Unreachable
from quackd.passport.link import CloseReason
from quackd.radio import Radio
from quackd.radio.links import Link, LinkDown, LinkInbox, LinkPolicy, LinkRefused, LinkTable
from quackd.radio.loop import LoopThread
from quackd.radio.proximity import ProximityEvent

#: Where a robot's talk inbox is on its passport server (`quackd.passport.serve.INBOX_PATH`).
INBOX_PATH = "/robot-passport/inbox"
#: Statuses that mean "not now" rather than "no": too many sessions, a server busy, and a robot
#: that has not yet heard this one by radio, which it will in a moment.
NOT_NOW = (403, 429, 503)


class TalkerPort(Protocol):
    """What the Meeter needs of a robot's Talker."""

    def meet(self, route: str, *, expect_beacon: str | None = None) -> str:
        """Say hello over `route` and return the other robot's did once the three steps are
        done. A `PassportError` is a refusal, not tried again for an hour; `Unreachable` and
        `LinkDown` are tried again later, and so is any error whose `status` is in `NOT_NOW`."""
        ...

    def leave(self, peer_did: str) -> None:
        """A bye to `peer_did`, best effort, and the session ended here whatever happens."""
        ...

    def inbox(self, wire: str, origin: str) -> tuple[int, str | None]:
        """A request that came in over HTTP (`origin` is `http://<ip>`) or a link (`ble://<id>`);
        the reply goes back the same way."""
        ...

    def peers_open(self) -> set[str]:
        """The dids of the robots this one has a session with now."""
        ...


@dataclass(frozen=True)
class MeetPolicy:
    """The clocks of meeting."""

    #: how long the robot with the higher beacon id waits for the lower to dial first
    grace_s: float = 15.0
    #: how long after each failed try the next waits, the last repeating, each times U(1, 1.25)
    backoff_s: tuple[float, ...] = (2.0, 4.0, 8.0, 16.0, 32.0, 60.0)
    busy_s: float = 30.0
    refused_s: float = 3600.0
    #: how long after a meeting ends the same robot is met again, if it is still near
    remeet_s: float = 120.0
    #: what happens when a robot met goes far: a bye, or the session kept until it goes quiet
    on_far: Literal["bye", "keep"] = "bye"
    #: meetings being set up at once
    max_meetings: int = 2
    #: how long one try may take before the planner counts it failed: longer than the slowest
    #: a try can be, two LAN posts of 5 s, a dial of up to 17 s and two link posts of up to 21 s
    attempt_s: float = 120.0
    #: how many robots and dids it remembers, at most
    remember: int = 1024


@dataclass(frozen=True)
class Meet:
    """Meet the robot that advertised `beacon_id` from `address`, now. `token` names this try,
    for its outcome to say which try it was."""

    beacon_id: str
    address: str
    flags: int
    attempt: int
    token: int = 0


@dataclass(frozen=True)
class Part:
    """The robot met went far: say bye to it and close every link to it."""

    beacon_id: str
    did: str | None
    route: str | None


@dataclass(frozen=True)
class Outcome:
    """What a try at meeting came to."""

    kind: Literal["met", "unreachable", "busy", "refused"]
    did: str | None = None
    route: str | None = None
    via: Literal["lan", "link"] | None = None
    reason: str = ""


State = Literal["waiting", "meeting", "met", "parting", "refused", "cooldown"]


@dataclass
class PeerPlan:
    """One robot near, or met, as the planner sees it."""

    beacon_id: str
    address: str
    flags: int
    state: State = "waiting"
    near: bool = True
    did: str | None = None
    route: str | None = None
    via: str | None = None
    attempts: int = 0
    next_at: float = 0.0
    started_at: float = 0.0
    #: links it dialled to this robot and still holds: it is meeting this robot, so wait
    inbound: int = 0
    reason: str = ""
    #: the try under way, or the last one: an outcome naming another is late
    token: int = 0


class Planner:
    """The robots near, and what to do about each, from proximity, links, sessions and how each
    try went. Pure but for a lock: every call takes `now`, and it is safe from any thread."""

    def __init__(
        self,
        me_beacon: Callable[[], str],
        policy: MeetPolicy | None = None,
        *,
        advertising: bool = True,
        rng: random.Random | None = None,
    ) -> None:
        self.me_beacon = me_beacon
        self.policy = policy or MeetPolicy()
        self.advertising = advertising
        self.rng = rng or random.Random()
        self._plans: dict[str, PeerPlan] = {}
        #: the did each beacon id turned out to be, learned in a hello
        self._did_of: dict[str, str] = {}
        #: until when a robot is not met again: by did, or by beacon id before a hello named it
        self._refused: dict[str, float] = {}
        self._cooldown: dict[str, float] = {}
        #: parts owed for meetings that finished after their robot had gone
        self._owed: list[Part] = []
        self._tokens = itertools.count(1)
        self._lock = threading.RLock()

    # ── what happens ─────────────────────────────────────────────────────────────────────

    def on_proximity(self, event: ProximityEvent) -> None:
        with self._lock:
            plan = self._plans.get(event.beacon_id)
            if event.kind == "seen":
                if plan is not None:
                    plan.address, plan.flags = event.address, event.flags
                return
            if event.kind == "near":
                if plan is None:
                    plan = self._plans[event.beacon_id] = PeerPlan(
                        event.beacon_id, event.address, event.flags
                    )
                    self._trim()
                plan.near, plan.address, plan.flags = True, event.address, event.flags
                if plan.state in ("waiting", "refused", "cooldown"):
                    self._wait(plan, event.at, fresh=True)
                return
            if plan is None:
                return
            plan.near = False
            if plan.state == "met":
                if self.policy.on_far == "bye":
                    plan.state, plan.reason = "parting", event.reason
            elif plan.state in ("waiting", "refused", "cooldown") and not plan.inbound:
                del self._plans[event.beacon_id]

    def on_link(self, beacon_id: str, *, opened: bool, inbound: bool, now: float) -> None:
        """A link opened or closed. One the other robot dialled means it is meeting this one,
        so this one waits rather than dial back."""
        if not inbound:
            return
        with self._lock:
            plan = self._plans.get(beacon_id)
            if plan is None:
                if not opened:
                    return
                plan = self._plans[beacon_id] = PeerPlan(beacon_id, "", 0, near=False)
                self._trim()
            plan.inbound = max(0, plan.inbound + (1 if opened else -1))
            if not opened and plan.inbound == 0 and plan.state == "waiting":
                if plan.near:
                    self._wait(plan, now, fresh=False)
                else:
                    del self._plans[beacon_id]

    def on_session(
        self, beacon_id: str | None, *, opened: bool, route: str | None, did: str, now: float
    ) -> None:
        """A session opened or ended, whichever robot said hello: what a meeting is for."""
        with self._lock:
            plan = self._plans.get(beacon_id) if beacon_id else None
            if plan is None:
                plan = next((p for p in self._plans.values() if p.did == did), None)
            if opened:
                if plan is None and beacon_id:
                    plan = self._plans[beacon_id] = PeerPlan(beacon_id, "", 0, near=False)
                    self._trim()
                if plan is None:
                    return
                plan.state, plan.did, plan.attempts = "met", did, 0
                plan.route = route or plan.route
                if route and plan.via is None:
                    plan.via = "lan" if route.startswith("http") else "link"
                self._did_of[plan.beacon_id] = did
                return
            if plan is None or plan.did != did or plan.state not in ("met", "parting"):
                return
            self._cooldown[did] = now + self.policy.remeet_s
            if plan.near:
                plan.state, plan.next_at, plan.reason = (
                    "cooldown",
                    now + self.policy.remeet_s,
                    "met",
                )
            else:
                del self._plans[plan.beacon_id]

    def on_outcome(
        self, beacon_id: str, outcome: Outcome, now: float, *, token: int | None = None
    ) -> None:
        """How a try the planner asked for went. `token` is the try's (`Meet.token`): a try that
        is not the one under way, or one that reports after the robot was met some other way,
        is heard only if it met the robot, since that session is real either way."""
        with self._lock:
            plan = self._plans.get(beacon_id)
            if outcome.did:
                self._did_of[beacon_id] = outcome.did
            if plan is None:
                if outcome.kind == "met":  # it went far while being met: part at once
                    self._owed.append(Part(beacon_id, outcome.did, outcome.route))
                return
            if plan.state in ("met", "parting"):
                # met already, by the other robot's hello: a failure now changes nothing
                if outcome.kind == "met":
                    plan.did = plan.did or outcome.did
                    plan.route, plan.via = plan.route or outcome.route, plan.via or outcome.via
                return
            late = plan.state != "meeting" or (token is not None and token != plan.token)
            if late and outcome.kind != "met":
                return
            plan.reason = outcome.reason or outcome.kind
            if outcome.kind == "met":
                plan.state, plan.did, plan.attempts = "met", outcome.did, 0
                plan.route, plan.via = outcome.route, outcome.via
                if not plan.near and self.policy.on_far == "bye":
                    plan.state = "parting"
                return
            if outcome.kind == "refused":
                until = now + self.policy.refused_s
                did = outcome.did or plan.did or self._did_of.get(beacon_id)
                self._refused[did or beacon_id] = until
                plan.state, plan.next_at = "refused", until
                return
            if outcome.kind == "busy":
                plan.state, plan.next_at = "waiting", now + self.policy.busy_s
            else:
                self._back_off(plan, now)
            if not plan.near and not plan.inbound:
                del self._plans[beacon_id]

    def _back_off(self, plan: PeerPlan, now: float) -> None:
        plan.attempts += 1
        step = self.policy.backoff_s[min(plan.attempts, len(self.policy.backoff_s)) - 1]
        plan.state, plan.next_at = "waiting", now + step * self.rng.uniform(1.0, 1.25)

    # ── what to do now ───────────────────────────────────────────────────────────────────

    def due(self, now: float) -> list[Meet | Part]:
        """Everything that is due: the robots to meet now, at most `max_meetings` at once, and
        the robots met that went far, to part from."""
        with self._lock:
            actions: list[Meet | Part] = list(self._owed)
            self._owed.clear()
            meeting = sum(1 for p in self._plans.values() if p.state == "meeting")
            for plan in sorted(self._plans.values(), key=lambda p: p.next_at):
                if plan.state == "parting":
                    actions.append(Part(plan.beacon_id, plan.did, plan.route))
                    if plan.did:
                        self._cooldown[plan.did] = now + self.policy.remeet_s
                    del self._plans[plan.beacon_id]
                elif plan.state == "meeting" and now - plan.started_at > self.policy.attempt_s:
                    # a try that never came back counts as failed, and the next waits its turn;
                    # what it reports late is heard only if it met the robot
                    self._back_off(plan, now)
                    plan.reason = "the last try never finished"
                    meeting -= 1
                elif plan.state in ("refused", "cooldown") and now >= plan.next_at:
                    plan.state = "waiting"
                    self._wait(plan, now, fresh=True)
                if (
                    plan.state == "waiting"
                    and plan.near
                    and not plan.inbound
                    and now >= plan.next_at
                    and meeting < self.policy.max_meetings
                ):
                    plan.state, plan.started_at, plan.token = "meeting", now, next(self._tokens)
                    meeting += 1
                    actions.append(
                        Meet(plan.beacon_id, plan.address, plan.flags, plan.attempts, plan.token)
                    )
            self._forget(now)
            return actions

    def peers(self) -> list[PeerPlan]:
        """Every robot the planner holds, as copies, for a status."""
        with self._lock:
            return [replace(p) for p in self._plans.values()]

    def did_of(self, beacon_id: str) -> str | None:
        with self._lock:
            return self._did_of.get(beacon_id)

    # ── the rules ────────────────────────────────────────────────────────────────────────

    def _wait(self, plan: PeerPlan, now: float, *, fresh: bool) -> None:
        """Put a robot near in line: held back while it is refused or just met, else due now if
        this robot dials first, or after the grace if the other should."""
        did = plan.did or self._did_of.get(plan.beacon_id)
        refused = max(self._refused.get(did or "", 0.0), self._refused.get(plan.beacon_id, 0.0))
        if refused > now:
            plan.state, plan.next_at, plan.reason = "refused", refused, "refused earlier"
            return
        cooldown = self._cooldown.get(did or "", 0.0)
        if cooldown > now:
            plan.state, plan.next_at, plan.reason = "cooldown", cooldown, "met a moment ago"
            return
        plan.state = "waiting"
        if fresh:
            first = not self.advertising or initiates(self.me_beacon(), plan.beacon_id)
            plan.next_at = now if first else now + self.policy.grace_s
        else:
            plan.next_at = max(plan.next_at, now)

    def _forget(self, now: float) -> None:
        for memory in (self._refused, self._cooldown):
            for key in [k for k, until in memory.items() if until <= now]:
                del memory[key]
        while len(self._did_of) > self.policy.remember:
            del self._did_of[next(iter(self._did_of))]

    def _trim(self) -> None:
        """At most `remember` robots in the table: the ones neither near nor met go first."""
        while len(self._plans) > self.policy.remember:
            idle = [p for p in self._plans.values() if p.state not in ("met", "meeting")]
            victim = min(idle or list(self._plans.values()), key=lambda p: p.next_at)
            del self._plans[victim.beacon_id]


@dataclass(frozen=True)
class MeetEvent:
    """What the Meeter did, for a person's log and the demo's narration."""

    kind: Literal["meeting", "met", "unreachable", "busy", "refused", "parted"]
    beacon_id: str
    via: Literal["lan", "link"] | None = None
    did: str | None = None
    route: str | None = None
    reason: str = ""


@dataclass
class Meeter:
    """Carries out what the planner decides: each meeting tried on a worker thread, over the
    LAN when the robot has a record that answers and over a link when it accepts one, and each
    robot gone far parted from. `step` is its clock; the service calls it a few times a second."""

    planner: Planner
    talker: TalkerPort
    links: LinkTable
    radio: Radio
    loop: LoopThread
    #: this robot's beacon id now
    me: Callable[[], str]
    #: the inbox for requests on a link this robot dials (`quackd.radio.links.talk_inbox`)
    inbox_for: Callable[[str], LinkInbox | None]
    lan: LanDirectory | None = None
    executor: Executor = field(
        default_factory=lambda: ThreadPoolExecutor(max_workers=4, thread_name_prefix="meet")
    )
    clock: Callable[[], float] = time.monotonic
    on_event: Callable[[MeetEvent], None] | None = None
    #: how long dialling a robot may take, then opening the link
    connect_s: float = 10.0

    def step(self, now: float | None = None) -> list[Meet | Part]:
        """Start whatever is due. What it started, for a log."""
        actions = self.planner.due(self.clock() if now is None else now)
        for action in actions:
            if isinstance(action, Meet):
                self._tell(MeetEvent("meeting", action.beacon_id))
                self.executor.submit(self._meet, action)
            else:
                self.executor.submit(self._part, action)
        return actions

    def _meet(self, action: Meet) -> None:
        try:
            outcome = self.attempt(action)
        except Exception as e:  # a bug here must not leave the robot "meeting" for a minute
            outcome = Outcome("unreachable", reason=f"the try failed: {e}")
        self.planner.on_outcome(action.beacon_id, outcome, self.clock(), token=action.token)
        self._tell(
            MeetEvent(
                outcome.kind,
                action.beacon_id,
                outcome.via,
                outcome.did,
                outcome.route,
                outcome.reason,
            )
        )

    def attempt(self, action: Meet) -> Outcome:
        """One try at meeting a robot: its LAN address if it has one that answers, else a link,
        else unreachable, with the reason each way failed. Runs on a worker thread."""
        why: list[str] = []
        if action.flags & FLAG_LAN and self.lan is not None:
            record = self.lan.lookup(action.beacon_id)
            if record is None:
                why.append("no LAN record carries its beacon id")
            else:
                host = f"[{record.host}]" if ":" in record.host else record.host
                route = f"http://{host}:{record.port}{INBOX_PATH}"
                try:
                    did = self.talker.meet(route, expect_beacon=action.beacon_id)
                except (Unreachable, LinkDown) as e:
                    why.append(f"its LAN address did not answer: {e}")
                except PassportError as e:
                    return self._refused_or_busy(e, route, "lan")
                else:
                    return Outcome("met", did, route, "lan")
        if not action.flags & FLAG_LINK:
            why.append("it accepts no link")
        elif not self.radio.status().ready:
            why.append(f"this robot's radio is {self.radio.status().state}")
        else:
            return self._over_a_link(action, why)
        return Outcome("unreachable", reason="; ".join(why))

    def _over_a_link(self, action: Meet, why: list[str]) -> Outcome:
        wait = self.connect_s + LinkPolicy().accept_s + 2.0
        try:
            # a link to this robot already open, from an earlier meeting either robot began,
            # carries this one too: a pair of robots that stays near and meets again every few
            # minutes would otherwise open a link each time and fill the four a robot may hold
            link = self.loop.call(self._open_link_to(action.beacon_id), 5.0) or self.loop.call(
                self.links.dial(
                    self.radio,
                    action.address,
                    me=self.me(),
                    expect=action.beacon_id,
                    inbox_for=self.inbox_for,
                    timeout=self.connect_s,
                ),
                wait,
            )
        except LinkRefused as e:
            return Outcome("busy" if e.busy else "refused", via="link", reason=str(e))
        except (LinkDown, TimeoutError) as e:
            return Outcome("unreachable", via="link", reason="; ".join([*why, str(e)]))
        try:
            did = self.talker.meet(link.route, expect_beacon=action.beacon_id)
        except (Unreachable, LinkDown) as e:
            self._close(link.peer_beacon)
            return Outcome("unreachable", via="link", reason="; ".join([*why, str(e)]))
        except PassportError as e:
            self._close(link.peer_beacon)
            return self._refused_or_busy(e, link.route, "link")
        # met over it: a link the other robot opened has its beacon id borne out now as well
        with contextlib.suppress(Exception):
            self.loop.call(self._confirm(link.id), 5.0)
        return Outcome("met", did, link.route, "link")

    async def _confirm(self, link_id: str) -> None:
        self.links.confirm(link_id)

    async def _open_link_to(self, beacon_id: str) -> Link | None:
        return next((link for link in self.links.by_beacon(beacon_id) if link.closed is None), None)

    def _refused_or_busy(
        self, error: PassportError, route: str, via: Literal["lan", "link"]
    ) -> Outcome:
        status = getattr(error, "status", None)
        kind: Literal["busy", "refused"] = "busy" if status in NOT_NOW else "refused"
        return Outcome(kind, None, route, via, f"{error.rule}: {error}")

    def _part(self, action: Part) -> None:
        if action.did:
            # a bye is a courtesy: the session ends here whatever happened to it
            with contextlib.suppress(Exception):
                self.talker.leave(action.did)
        self._close(action.beacon_id)
        self._tell(MeetEvent("parted", action.beacon_id, did=action.did, route=action.route))

    def _close(self, beacon_id: str) -> None:
        # a loop that is stopping closes every link anyway
        with contextlib.suppress(Exception):
            self.loop.call(self.links.close_beacon(beacon_id, CloseReason.FAR), 5.0)

    def _tell(self, event: MeetEvent) -> None:
        if self.on_event is not None:
            self.on_event(event)

    def shutdown(self) -> None:
        """Let the worker threads go, abandoning any try still running; for the service's end."""
        self.executor.shutdown(wait=False, cancel_futures=True)


__all__ = [
    "INBOX_PATH",
    "NOT_NOW",
    "Meet",
    "MeetEvent",
    "MeetPolicy",
    "Meeter",
    "Outcome",
    "Part",
    "PeerPlan",
    "Planner",
    "TalkerPort",
]
