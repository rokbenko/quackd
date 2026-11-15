"""Who kicks: a Contract Net auction, pure and synchronous so it is trivially testable.

Lowest camera-estimated ball distance wins. Ties break on the member name, so two bids in
the same instant are a non-event. A previous kicker keeps the claim unless a challenger
undercuts it by the hysteresis margin (RoboCup's anti-oscillation rule). Failed kickers
sit out a cooldown before bidding again.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from roboglot.glotfile.schema import TeamRole, TeamSection
from roboglot.team.messages import BidMsg

LONGEST_VERB_SLEEP_S = 1.5
"""The kick verb parks in one unbroken 1.5 s sim sleep, during which no HB can go out."""


@dataclass(frozen=True)
class AuctionPolicy:
    window_s: float = 0.4  # sim time collected after the FIRST bid
    hysteresis: float = 0.2  # challenger must be >= this fraction closer to unseat
    lease_s: float = 6.0
    min_sep_m: float = 0.4
    cooldown_s: float = 3.0  # a failed kicker sits out this long
    hb_timeout_s: float = 3.0  # watchdog: sim seconds without a bus HB -> presumed dead

    @classmethod
    def from_team(cls, team: TeamSection) -> AuctionPolicy:
        # the watchdog must survive a healthy duck's longest single verb sleep plus one
        # heartbeat period, or a small (valid) heartbeat setting kills every kicker mid-kick
        hb = team.safety.per_duck_heartbeat_s
        return cls(
            hysteresis=team.allocation.hysteresis_pct / 100.0,
            lease_s=team.allocation.claim_lease_s,
            min_sep_m=team.safety.min_separation_m,
            hb_timeout_s=max(3.0 * hb, hb + LONGEST_VERB_SLEEP_S + 1.0),
        )


@dataclass
class AuctionDecision:
    kicker: str
    winning_dist: float
    bids: dict[str, float]
    tie: bool
    hysteresis_applied: bool


@dataclass
class Auction:
    policy: AuctionPolicy
    now: Callable[[], float]
    opened_at: float | None = None
    bids: dict[str, float] = field(default_factory=dict)

    @property
    def is_open(self) -> bool:
        return self.opened_at is not None

    def open(self, first: BidMsg) -> None:
        self.opened_at = self.now()
        self.bids = {}
        self.add(first)

    def add(self, bid: BidMsg) -> None:
        best = self.bids.get(bid.src)
        if best is None or bid.ball_dist_m < best:
            self.bids[bid.src] = bid.ball_dist_m

    def due(self) -> bool:
        return self.is_open and self.now() >= (self.opened_at or 0.0) + self.policy.window_s

    def decide(self, prev_kicker: str | None, excluded: set[str]) -> AuctionDecision | None:
        """Close the auction and pick a kicker, or None if every bidder is excluded."""
        candidates = {duck: d for duck, d in self.bids.items() if duck not in excluded}
        self.opened_at = None
        if not candidates:
            return None
        winner = min(candidates, key=lambda duck: (candidates[duck], duck))
        best = candidates[winner]
        tie = sum(1 for d in candidates.values() if d == best) > 1
        hysteresis_applied = False
        if prev_kicker is not None and prev_kicker in candidates and winner != prev_kicker:
            prev_dist = candidates[prev_kicker]
            if best >= (1.0 - self.policy.hysteresis) * prev_dist:
                winner, best = prev_kicker, prev_dist
                hysteresis_applied = True
        return AuctionDecision(
            kicker=winner,
            winning_dist=best,
            bids=dict(candidates),
            tie=tie,
            hysteresis_applied=hysteresis_applied,
        )


# ── roles (0.4): one auction over several roles, decided together ───────────────────────


@dataclass
class RoleDecision:
    assignments: dict[str, str]
    """role -> member, held roles included."""
    costs: dict[str, float]
    """role -> the winning own-distance (a held role keeps its last bid)."""
    bids: dict[str, dict[str, float]]
    """role -> member -> best distance, everything that was on the table."""
    ties: list[str]
    hysteresis_applied: list[str]

    @property
    def kicker(self) -> str | None:
        return self.assignments.get("kicker")


@dataclass
class RoleAuction:
    """Contract Net over roles. Same window, same lowest-own-distance rule and the same
    member-name tie-break as `Auction`; roles are filled most-constrained first (fewest
    eligible bidders, then role name), one member per role. Deterministic: every ordering
    is a sort over strings or (float, str) pairs, so bid arrival order cannot matter."""

    policy: AuctionPolicy
    now: Callable[[], float]
    roles: Mapping[str, TeamRole]
    opened_at: float | None = None
    bids: dict[str, dict[str, float]] = field(default_factory=dict)
    held: dict[str, str] = field(default_factory=dict)
    """Roles kept across auctions (the spotter: its reference frame must not change)."""

    @property
    def is_open(self) -> bool:
        return self.opened_at is not None

    def open(self, first: BidMsg) -> None:
        self.opened_at = self.now()
        self.bids = {}
        self.add(first)

    def add(self, bid: BidMsg) -> None:
        if bid.role is None or bid.role not in self.roles:
            return
        table = self.bids.setdefault(bid.role, {})
        best = table.get(bid.src)
        if best is None or bid.ball_dist_m < best:
            table[bid.src] = bid.ball_dist_m

    def due(self) -> bool:
        return self.is_open and self.now() >= (self.opened_at or 0.0) + self.policy.window_s

    def unfilled(self) -> list[str]:
        return sorted(role for role in self.roles if role not in self.held)

    def _candidates(self, role: str, excluded: set[str], taken: set[str]) -> dict[str, float]:
        return {
            src: dist
            for src, dist in self.bids.get(role, {}).items()
            if src not in excluded and src not in taken
        }

    def complete(self, excluded: set[str]) -> bool:
        """Could every unfilled role be filled right now (one member per role)? A dry run
        of `decide`, so a lone robot bidding for two roles keeps the window open."""
        return self._assign({}, excluded) is not None

    def decide(self, prev: Mapping[str, str], excluded: set[str]) -> RoleDecision | None:
        """Close the auction and fill every unfilled role, or None if one cannot be filled."""
        self.opened_at = None
        return self._assign(prev, excluded)

    def _assign(self, prev: Mapping[str, str], excluded: set[str]) -> RoleDecision | None:
        assignments = dict(self.held)
        taken = set(self.held.values())
        costs: dict[str, float] = {}
        ties: list[str] = []
        hysteresis: list[str] = []
        order = sorted(
            self.unfilled(), key=lambda r: (len(self._candidates(r, excluded, taken)), r)
        )
        for role in order:
            candidates = self._candidates(role, excluded, taken)
            if not candidates:
                return None
            winner = min(candidates, key=lambda src: (candidates[src], src))
            best = candidates[winner]
            if sum(1 for d in candidates.values() if d == best) > 1:
                ties.append(role)
            previous = prev.get(role)
            keeps_claim = (
                previous is not None
                and previous in candidates
                and winner != previous
                and best >= (1.0 - self.policy.hysteresis) * candidates[previous]
            )
            if keeps_claim and previous is not None:
                winner, best = previous, candidates[previous]
                hysteresis.append(role)
            assignments[role] = winner
            costs[role] = best
            taken.add(winner)
        return RoleDecision(
            assignments=assignments,
            costs=costs,
            bids={role: dict(table) for role, table in self.bids.items()},
            ties=ties,
            hysteresis_applied=hysteresis,
        )
