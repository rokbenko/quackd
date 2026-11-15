"""The referee: opens auctions, grants the one claim, watches heartbeats, judges outcome.

The coordinator is deterministic code on the same lockstep clock as the robots
(participant "coordinator", 0.05 s sim ticks), so bid windows, leases and the watchdog are
measured in sim time and a slow LLM cannot warp them. In a homogeneous team a kicker's
`RESULT kicked` ends the run and the runner vetoes it against sim ground truth. With
roles (0.4, ADR-0020) the actor only reports `kick_done`; the spotter judges from its own
fresh frames and publishes a `VERDICT`, and only `moved` is a success. The runner's ground
truth veto stays on top in both cases.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
from dataclasses import dataclass, field
from typing import Any, Literal

from roboglot.log import EventLog
from roboglot.sim2d.clock import HookInterrupt, TeamClock
from roboglot.team.auction import Auction, AuctionPolicy, RoleAuction
from roboglot.team.bus import Bus
from roboglot.team.capability import missing, missing_needs_in
from roboglot.team.member import TeamMember
from roboglot.team.messages import (
    BidMsg,
    ClaimMsg,
    HbMsg,
    Hint,
    HintMsg,
    ResultMsg,
    RoleMsg,
    TaskMsg,
    TeamTask,
    VerdictMsg,
    Wedge,
)
from roboglot.team.transcript import TeamTranscript

TeamOutcome = Literal["success", "failure", "budget", "aborted", "error"]
COORD_TICK_S = 0.05
COORD_PID = "coordinator"
SPOTTER = "spotter"
KICKER = "kicker"


@dataclass
class TeamCoordinator:
    task: TeamTask
    members: dict[str, TeamMember]
    wedges: dict[str, Wedge]
    bus: Bus
    clock: TeamClock
    transcript: TeamTranscript
    policy: AuctionPolicy = field(default_factory=AuctionPolicy)
    success_moved_m: float = 0.3
    log: Any = lambda *_: None
    on_event: Any = None
    """Optional callback (kind: str, data: dict) for a recorder or a live view."""
    event_log: EventLog | None = None
    """The same events as `LogEvent`s, for a view and never a record: team.jsonl already
    carries every one of these kinds under its own name, written on the line above."""

    def __post_init__(self) -> None:
        self.abort = asyncio.Event()  # the kill switch binds here
        self.sub = self.bus.subscribe(COORD_PID)
        self.auction = Auction(self.policy, self.clock.now)
        self.roles = dict(self.task.roles)
        self.assigner = RoleAuction(self.policy, self.clock.now, self.roles) if self.roles else None
        self.assignments: dict[str, str] = {}
        self.spotter: str | None = None
        self.phase = "search"
        self.judge_deadline: float | None = None
        self.verdicts: list[dict[str, Any]] = []
        self.latest_hint: Hint | None = None
        self._seq = 0
        self._waiting_logged = False
        self.kicker: str | None = None
        self.prev_kicker: str | None = None
        self.lease_deadline: float | None = None
        self.last_hb: dict[str, float] = {}
        self.excluded_until: dict[str, float] = {}
        self.auctions = 0
        self.bids = 0
        self.search_rounds = 0
        self.searching_empty: set[str] = set()
        self._sep_warned: dict[str, float] = {}
        self.outcome: TeamOutcome = "error"
        self.reason = "coordinator exited unexpectedly"

    # ── helpers ─────────────────────────────────────────────────────────────────────

    def _now(self) -> float:
        return self.clock.now()

    def _publish(self, msg: Any) -> None:
        self.bus.publish(msg)

    def _event(self, kind: str, **data: Any) -> None:
        """The coordinator's decisions to whoever is watching: the event_log's views first, then
        the recorder's callback.

        There is no try/except here on purpose. `EventLog.emit` swallows and counts an
        observer's exception, which is what makes a view safe to attach; `on_event` is the
        recorder's own contract and a raising callback there would end the run."""
        if self.event_log is not None:
            self.event_log.emit(kind, **data)
        if self.on_event is not None:
            self.on_event(kind, data)

    def _entity(self, name: str) -> tuple[str, int] | None:
        """Which camera shows this member: ("duck", i), for the recorder."""
        transport = self.members[name].transport
        index = getattr(transport, "duck_index", None)
        return ("duck", int(index)) if index is not None else None

    def _mobile(self, name: str) -> bool:
        return getattr(self.members[name].transport, "mobility", "legged") != "none"

    def _role(
        self,
        duck: str,
        role: str,
        wedge: Wedge | None = None,
        *,
        retreat: bool = False,
        team_role: str | None = None,
        hint: Hint | None = None,
        kicker: str | None = None,
    ) -> None:
        if self.roles:
            self._seq += 1  # legacy teams keep seq 0: their messages are unchanged
        self._publish(
            RoleMsg(
                t=self._now(),
                src=COORD_PID,
                task_id=self.task.task_id,
                duck=duck,
                role=role,  # type: ignore[arg-type]
                wedge=wedge,
                min_sep_m=self.policy.min_sep_m,
                retreat=retreat,
                team_role=team_role,
                seq=self._seq,
                hint=hint,
                kicker=kicker,
            )
        )

    def _excluded_now(self) -> set[str]:
        now = self._now()
        return {d for d, until in self.excluded_until.items() if until > now}

    def _live(self) -> set[str]:
        return {name for name in self.members if self.excluded_until.get(name, 0.0) != math.inf}

    def _held(self) -> set[str]:
        return set(self.assigner.held.values()) if self.assigner is not None else set()

    def _exclude(self, duck: str, seconds: float) -> None:
        # never SHORTEN an exclusion: a late RESULT must not resurrect a presumed-dead duck
        until = math.inf if seconds == math.inf else self._now() + seconds
        self.excluded_until[duck] = max(self.excluded_until.get(duck, 0.0), until)
        if until == math.inf and self.assigner is not None:
            # a dead or spent holder gives its role back; another eligible robot may take it
            for role, holder in list(self.assigner.held.items()):
                if holder == duck:
                    del self.assigner.held[role]
                    self.assignments.pop(role, None)
                    if role == SPOTTER:
                        self.spotter = None

    # ── dispatch ────────────────────────────────────────────────────────────────────

    def _dispatch(self, msg: Any) -> None:
        if isinstance(msg, BidMsg):
            if self.roles:
                self._on_bid_roles(msg)
                return
            self.bids += 1
            self.searching_empty.discard(msg.src)  # a sighting outranks an earlier empty scan
            if msg.src in self._excluded_now():
                return  # the cooldown gates bidding itself, not just winning at decide time
            if self.kicker is not None:
                return  # standby sighting while a claim is live
            if not self.auction.is_open:
                self.auction.open(msg)
                self.transcript.write("auction_open", first_bid=msg.src, dist=msg.ball_dist_m)
                self._event("auction", first_bid=msg.src, dist=msg.ball_dist_m)
            else:
                self.auction.add(msg)
        elif isinstance(msg, HbMsg):
            self.last_hb[msg.src] = msg.t
        elif isinstance(msg, ResultMsg):
            self._on_result(msg)
        elif isinstance(msg, HintMsg):
            self.latest_hint = msg.hint
        elif isinstance(msg, VerdictMsg):
            self._on_verdict(msg)

    def _on_bid_roles(self, msg: BidMsg) -> None:
        assert self.assigner is not None
        self.bids += 1
        self.searching_empty.discard(msg.src)
        if msg.role is None or msg.role not in self.roles:
            self.transcript.write("bid_rejected", src=msg.src, role=msg.role, why="unknown role")
            self._event("bid_rejected", src=msg.src, role=msg.role, why="unknown role")
            return
        role = self.roles[msg.role]
        lacking = missing(role.requires, msg.provides)
        if not lacking and role.needs:
            # the physical half of the same question, judged from what the bid itself carries
            # so a robot we do not run is held to the same standard. A bid with no datasheet
            # is a body that said nothing, and a body that said nothing does not qualify.
            lacking = missing_needs_in(role.needs, msg.datasheet or {}, msg.mobility)
        if lacking:
            # defence in depth: the member already checked; a LAN peer might not have
            self.transcript.write("bid_rejected", src=msg.src, role=msg.role, missing=lacking)
            self._event("bid_rejected", src=msg.src, role=msg.role, missing=lacking)
            return
        if msg.src in self._excluded_now() or self.kicker is not None:
            return
        if msg.role in self.assigner.held:
            return  # the role is held (the spotter keeps its reference frame)
        if not self.assigner.is_open:
            self.assigner.open(msg)
            self._waiting_logged = False
            self.transcript.write(
                "auction_open", first_bid=msg.src, role=msg.role, dist=msg.ball_dist_m
            )
            self._event("auction", first_bid=msg.src, dist=msg.ball_dist_m)
        else:
            self.assigner.add(msg)

    def _on_result(self, msg: ResultMsg) -> None:
        if msg.status in ("kicked", "kick_done") and self.roles:
            if msg.src != self.kicker:
                self.transcript.write("result_ignored", src=msg.src, status=msg.status)
                return
            # the actor reports; the spotter judges. Never a success on the actor's word.
            self.lease_deadline = None
            self.phase = "judging"
            self.judge_deadline = self._now() + self.task.judge_timeout_s
            self.transcript.write(
                "kick_done", kicker=msg.src, self_reported_moved_m=msg.ball_moved_m
            )
            self._event("kick_done", kicker=msg.src)
            if self.spotter is not None:
                self._role(self.spotter, "JUDGE", team_role=SPOTTER, kicker=msg.src)
            return
        if msg.status == "kicked":
            self.outcome = "success"
            moved = msg.ball_moved_m if msg.ball_moved_m is not None else 0.0
            self.reason = f"{msg.src} kicked the ball {moved:.2f} m (auction {self.auctions})"
        elif msg.status in ("miss", "fell"):
            cooldown = math.inf if msg.status == "fell" else self.policy.cooldown_s
            self._miss(msg.src, f"{msg.status}: {msg.detail}", cooldown)
        elif msg.status == "search_empty":
            self.searching_empty.add(msg.src)
        elif msg.status in ("budget", "aborted"):
            self._exclude(msg.src, math.inf)
            self.transcript.write("member_excluded", duck=msg.src, why=msg.status)
            self._event("member_excluded", duck=msg.src, why=msg.status)
            if msg.src == self.kicker:
                self._miss(msg.src, msg.status, math.inf)

    def _on_verdict(self, msg: VerdictMsg) -> None:
        if msg.src != self.spotter or self.phase != "judging" or msg.kicker != self.kicker:
            self.transcript.write("verdict_ignored", src=msg.src, verdict=msg.verdict)
            return
        record = {
            "spotter": msg.src,
            "kicker": msg.kicker,
            "verdict": msg.verdict,
            "moved_m": msg.moved_m,
            "frames": msg.frames,
        }
        self.verdicts.append(record)
        self.transcript.write("verdict", **record)
        self._event("verdict", **record)
        self.judge_deadline = None
        self.phase = "search"
        if msg.verdict == "moved":
            self.outcome = "success"
            moved = msg.moved_m if msg.moved_m is not None else 0.0
            self.reason = (
                f"{msg.src} judged the ball moved {moved:.2f} m after {msg.kicker}'s kick "
                f"(auction {self.auctions})"
            )
            return
        detail = (
            f"spotter judged not moved (est {msg.moved_m:.2f} m)"
            if msg.verdict == "not_moved" and msg.moved_m is not None
            else "spotter lost the ball after the kick"
        )
        self._miss(msg.kicker, detail, self.policy.cooldown_s)

    def _miss(self, duck: str, detail: str, cooldown: float) -> None:
        self.transcript.write("miss", duck=duck, detail=detail)
        self._event("miss", duck=duck, detail=detail)
        self._exclude(duck, cooldown)
        if duck == self.kicker:
            self.prev_kicker = self.kicker
            self.kicker = None
            self.lease_deadline = None
        self.phase = "search"
        self.judge_deadline = None
        self.searching_empty.clear()
        # the ball moved: wedges are stale, so every live duck re-scans the full circle
        # (cooldown exclusion gates the AUCTION, not the searching); a held spotter keeps
        # watching instead of re-searching, its reference frame must not move
        for name in self._live() - self._held():
            self._role(name, "SEARCH", None)

    # ── phases ──────────────────────────────────────────────────────────────────────

    def _decide_if_due(self) -> None:
        if self.roles:
            self._decide_roles_if_due()
            return
        if self.kicker is not None or not self.auction.due():
            return
        decision = self.auction.decide(self.prev_kicker, self._excluded_now())
        self.auctions += 1
        if decision is None:
            self.transcript.write("auction_void", auctions=self.auctions)
            self._event("auction_void", auctions=self.auctions)
            return
        self.transcript.write(
            "auction_decision",
            kicker=decision.kicker,
            winning_dist=decision.winning_dist,
            bids=decision.bids,
            tie=decision.tie,
            hysteresis_applied=decision.hysteresis_applied,
        )
        self.kicker = decision.kicker
        self._event("claim", kicker=decision.kicker, dist=decision.winning_dist)
        self.lease_deadline = self._now() + self.policy.lease_s
        self._publish(
            ClaimMsg(
                t=self._now(),
                src=COORD_PID,
                task_id=self.task.task_id,
                kicker=decision.kicker,
                lease_s=self.policy.lease_s,
            )
        )
        self._role(decision.kicker, "KICK")
        for name in self.members:
            if name != decision.kicker:
                self._role(name, "YIELD")

    def _decide_roles_if_due(self) -> None:
        assert self.assigner is not None
        if self.kicker is not None or not self.assigner.due():
            return
        excluded = self._excluded_now()
        if not self.assigner.complete(excluded):
            if not self._waiting_logged:
                self._waiting_logged = True
                missing_roles = self.assigner.unfilled()
                self.transcript.write("auction_waiting", missing_roles=missing_roles)
                self._event("auction_waiting", missing_roles=missing_roles)
            return  # the window stays open until every role has a bidder
        prev = {KICKER: self.prev_kicker} if self.prev_kicker else {}
        decision = self.assigner.decide(prev, excluded)
        self.auctions += 1
        if decision is None or decision.kicker is None:
            self.transcript.write("auction_void", auctions=self.auctions)
            self._event("auction_void", auctions=self.auctions)
            return
        kicker = decision.kicker
        newly_spotter = SPOTTER in decision.assignments and SPOTTER not in self.assigner.held
        self.assignments = dict(decision.assignments)
        self.spotter = decision.assignments.get(SPOTTER)
        if self.spotter is not None:
            self.assigner.held[SPOTTER] = self.spotter
        self.transcript.write(
            "auction_decision",
            kicker=kicker,
            winning_dist=decision.costs.get(KICKER),
            bids=decision.bids.get(KICKER, {}),
            tie=KICKER in decision.ties,
            hysteresis_applied=KICKER in decision.hysteresis_applied,
            assignments=self.assignments,
            costs=decision.costs,
            role_bids=decision.bids,
        )
        self.kicker = kicker
        self.phase = "assigned"
        self._event(
            "claim",
            kicker=kicker,
            dist=decision.costs.get(KICKER, 0.0),
            spotter=self.spotter,
            entity=self._entity(kicker),
        )
        self.lease_deadline = self._now() + self.policy.lease_s
        self._publish(
            ClaimMsg(
                t=self._now(),
                src=COORD_PID,
                task_id=self.task.task_id,
                kicker=kicker,
                lease_s=self.policy.lease_s,
                assignments=self.assignments,
            )
        )
        hint = self.latest_hint if self.task.frame_hints else None
        self._role(kicker, "KICK", team_role=KICKER, hint=hint)
        if self.spotter is not None and newly_spotter:
            self._role(self.spotter, "SPOT", team_role=SPOTTER)
        for name in self.members:
            if name not in (kicker, self.spotter):
                self._role(name, "YIELD")

    def _watchdog(self) -> None:
        now = self._now()
        for name in list(self._live()):
            seen = self.last_hb.get(name)
            if seen is not None and now - seen > self.policy.hb_timeout_s:
                self._exclude(name, math.inf)
                self.transcript.write("member_dead", duck=name, last_hb=seen)
                self._event("member_dead", duck=name, last_hb=seen)
                if name == self.kicker:
                    self._miss(name, "heartbeat lost while holding the claim", math.inf)

    def _enforce_separation(self) -> None:
        """Ground truth guard: while a claim is live, a non-kicker inside the separation
        ring around the kicker gets a retreat order (motion still runs through that
        duck's own executor, the coordinator never moves anyone directly). A robot that
        cannot move never intrudes and is never ordered back."""
        if self.kicker is None or self.kicker not in self.members:
            return
        kicker = self.members[self.kicker].transport
        if getattr(kicker, "duck_index", None) is None:
            return
        kd = kicker.world.ducks[kicker.duck_index]
        now = self._now()
        for name, m in self.members.items():
            if name == self.kicker or self.excluded_until.get(name, 0.0) == math.inf:
                continue
            if not self._mobile(name):
                continue
            d = m.transport.world.ducks[m.transport.duck_index]
            dist = math.hypot(d.x - kd.x, d.y - kd.y)
            if dist < self.policy.min_sep_m and now - self._sep_warned.get(name, -1e9) >= 1.0:
                self._sep_warned[name] = now
                self.transcript.write("separation_hold", duck=name, dist_m=round(dist, 3))
                self._event("separation", duck=name, dist=dist)
                self._role(name, "YIELD", retreat=True)

    def _rotate_wedges_if_empty(self) -> bool:
        """All live searchers came up empty: rotate the partition and try again."""
        live = self._live() - self._excluded_now() - self._held()
        auction_open = self.assigner.is_open if self.assigner is not None else self.auction.is_open
        if (
            self.kicker is not None
            or auction_open  # a live sighting is being decided: not empty
            or not live
            or not live <= self.searching_empty
        ):
            return True
        self.search_rounds += 1
        if self.search_rounds >= self.task.max_search_rounds:
            self.outcome = "failure"
            self.reason = (
                f"no {self.task.target} found by any duck after {self.search_rounds} search rounds"
            )
            return False
        if self.wedges:
            half = next(iter(self.wedges.values())).width_deg / 2
            self.wedges = {
                name: Wedge(start_deg=w.start_deg + half, end_deg=w.end_deg + half)
                for name, w in self.wedges.items()
            }
        else:
            half = 0.0
        self.searching_empty.clear()
        self.transcript.write("wedges_rotated", round=self.search_rounds, by_deg=half)
        self._event("wedges_rotated", round=self.search_rounds, by_deg=half)
        for name in live:
            self._role(name, "SEARCH", self.wedges.get(name))
        return True

    # ── the run ─────────────────────────────────────────────────────────────────────

    async def run(self) -> tuple[TeamOutcome, str]:
        self.clock.register(COORD_PID)
        member_tasks = [
            asyncio.create_task(m.run(), name=f"team-{name}") for name, m in self.members.items()
        ]
        self._publish(
            TaskMsg(
                t=self._now(),
                src=COORD_PID,
                task_id=self.task.task_id,
                task=self.task,
                members=sorted(self.members),
            )
        )
        for name in self.members:
            self._role(name, "SEARCH", self.wedges.get(name))
        t0 = self._now()
        for name in self.members:
            # seed the watchdog: a duck that dies before its FIRST heartbeat must still
            # be declared dead instead of blocking wedge rotation until the timeout
            self.last_hb.setdefault(name, t0)
        self.outcome = "error"
        try:
            while True:
                for msg in self.sub.drain():
                    self._dispatch(msg)
                if self.outcome == "success":
                    break
                self._watchdog()
                self._decide_if_due()
                self._enforce_separation()
                if (
                    self.kicker is not None
                    and self.lease_deadline is not None
                    and self._now() > self.lease_deadline
                ):
                    self._miss(self.kicker, "claim lease expired", self.policy.cooldown_s)
                if (
                    self.kicker is not None
                    and self.judge_deadline is not None
                    and self._now() > self.judge_deadline
                ):
                    self._miss(
                        self.kicker, "no verdict from the spotter in time", self.policy.cooldown_s
                    )
                if not self._rotate_wedges_if_empty():
                    break
                if (
                    self.roles
                    and self.spotter is None
                    and not any(self._mobile(n) is False for n in self._live())
                    and not any(
                        not missing(self.roles[SPOTTER].requires, m.provides)
                        for n, m in self.members.items()
                        if n in self._live()
                    )
                ):
                    self.outcome = "failure"
                    self.reason = "no live robot can take the spotter role"
                    break
                if self.abort.is_set():
                    self.outcome = "aborted"
                    self.reason = "kill switch"
                    for m in self.members.values():
                        m.executor.abort.set()
                    break
                if not self._live():
                    self.outcome = "failure"
                    self.reason = "every duck is excluded (dead, fallen or out of budget)"
                    break
                if self._now() - t0 > self.task.timeout_s:
                    self.outcome = "failure"
                    self.reason = f"global timeout after {self.task.timeout_s:g}s of sim time"
                    break
                try:
                    await self.clock.sleep(COORD_PID, COORD_TICK_S)
                except HookInterrupt as e:
                    self.outcome = "aborted"
                    self.reason = str(e) or "interrupted"
                    for m in self.members.values():
                        m.executor.abort.set()
                    break
        finally:
            for name in self.members:
                self._role(name, "STOP")
            # let members wind down on sim time, then hard-abort stragglers
            for _ in range(40):
                if all(t.done() for t in member_tasks):
                    break
                with contextlib.suppress(Exception):
                    await self.clock.sleep(COORD_PID, COORD_TICK_S)
            for m in self.members.values():
                m.executor.abort.set()
            self.clock.unregister(COORD_PID)
            await asyncio.gather(*member_tasks, return_exceptions=True)
            await self.clock.stop()
        return self.outcome, self.reason
