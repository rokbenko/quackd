"""Proximity: which robots are near, decided from how loud their adverts are, and nothing else.

A robot's service meets the robots its radio says are near, so near has to be decided steadily:
not on one loud advert, and not lost to one quiet one. Bluetooth signal strength is noisy in a
particular way. Multipath and the three advertising channels give dips of 10 to 20 dB that last
one advert, and a body in between gives a few dB more, so the smoothing here is a median over the
last three seconds, which a dip moves not at all, rather than an average, which every dip drags
down. A robot is near once that median has stayed at or above the threshold for a second, and far
again only once it has stayed 6 dB below it for five seconds, or nothing has been heard of it for
eight, or the link to it dropped. The 6 dB between the two is so that a robot standing at the
threshold is not met and left over and over.

The threshold comes from a distance, `meet_within_m`, through the log-distance model: -59 dBm at
a metre and a path loss exponent of 2.2 put 2 m at -65.6 dBm. Neither number has been measured
on a robot, and RSSI is weak evidence of distance at the best of times, which is why near only
ever starts a hello and a passport check, and never decides anything a robot would do.

Pure and synchronous, with the clock passed in: the service feeds it sightings and ticks it, and
the tests turn the clock by hand.
"""

from __future__ import annotations

import math
import statistics
from collections import deque
from dataclasses import dataclass, replace
from typing import Literal

from quackd.radio import Sighting

#: Clocks are floats, and five adverts a second do not add up to exactly a second.
_EPSILON = 1e-6


@dataclass(frozen=True)
class ProximityPolicy:
    """What near means: within `meet_within_m` by the model, held for `near_hold_s`; and far:
    `hysteresis_db` quieter for `far_hold_s`, or unheard for `stale_s`."""

    meet_within_m: float = 2.0
    rssi_at_1m: float = -59.0
    path_loss_n: float = 2.2
    hysteresis_db: float = 6.0
    window_s: float = 3.0
    min_samples: int = 3
    max_samples: int = 16
    near_hold_s: float = 1.0
    far_hold_s: float = 5.0
    stale_s: float = 8.0
    #: how often a robot still in earshot is reported again as seen, for a status and a log
    seen_every_s: float = 10.0

    @property
    def near_dbm(self) -> float:
        return self.rssi_at_1m - 10 * self.path_loss_n * math.log10(self.meet_within_m)

    @property
    def far_dbm(self) -> float:
        return self.near_dbm - self.hysteresis_db

    def metres(self, rssi: float) -> float:
        """The distance the model gives a signal strength: an estimate, never a measurement."""
        return 10 ** ((self.rssi_at_1m - rssi) / (10 * self.path_loss_n))

    def scaled(self, speed: float) -> ProximityPolicy:
        """The same policy for a sim scene that runs `speed` times faster than the clock, so
        that a hold of a second is a second of the scene. For the sim's fast demo only."""
        if speed <= 0:
            raise ValueError("a scene runs forward")
        return replace(
            self,
            window_s=self.window_s / speed,
            near_hold_s=self.near_hold_s / speed,
            far_hold_s=self.far_hold_s / speed,
            stale_s=self.stale_s / speed,
            seen_every_s=self.seen_every_s / speed,
        )


@dataclass(frozen=True)
class ProximityEvent:
    """A robot first heard or heard again (`seen`), come near, or gone far, and why."""

    kind: Literal["seen", "near", "far"]
    beacon_id: str
    address: str
    flags: int
    rssi: float | None
    metres: float | None
    at: float
    reason: str


@dataclass(frozen=True)
class PeerView:
    """One robot the radio has heard, as a status shows it."""

    beacon_id: str
    address: str
    flags: int
    near: bool
    rssi: float | None
    metres: float | None
    heard_s_ago: float
    held: bool
    #: the sim's own distance, for its narration; None from a real radio
    true_m: float | None


@dataclass
class _Peer:
    beacon_id: str
    address: str
    flags: int
    samples: deque[tuple[float, float]]
    last_heard: float
    last_seen_event: float
    near: bool = False
    above_since: float | None = None
    below_since: float | None = None
    true_m: float | None = None


class Tracker:
    """Every robot heard, near or far, at most `max_peers` of them. Not thread-safe: it lives on
    the radio's loop, which is where sightings arrive."""

    def __init__(
        self, policy: ProximityPolicy | None = None, *, me: str | None = None, max_peers: int = 256
    ) -> None:
        self.policy = policy or ProximityPolicy()
        #: this robot's own beacon id, never a peer; the service updates it on each rotation
        self.me = me
        self.max_peers = max_peers
        self._peers: dict[str, _Peer] = {}
        #: links open to each robot, by beacon id, whether or not an advert of it was heard yet:
        #: a link can open before the first advert this end hears
        self._holds: dict[str, int] = {}
        self._paused_since: float | None = None

    # ── what arrives ─────────────────────────────────────────────────────────────────────

    def observe(self, sighting: Sighting) -> list[ProximityEvent]:
        """Take one advert heard; the events it causes."""
        if sighting.beacon_id == self.me or not math.isfinite(sighting.rssi):
            return []
        now = sighting.at
        peer = self._peers.get(sighting.beacon_id)
        events: list[ProximityEvent] = []
        if peer is None:
            if not self._make_room():
                return []  # every robot in the table is near or held: a flood, not a crowd
            peer = _Peer(
                sighting.beacon_id,
                sighting.address,
                sighting.flags,
                deque(maxlen=self.policy.max_samples),
                now,
                now,
            )
            self._peers[sighting.beacon_id] = peer
            events.append(self._event("seen", peer, now, "first heard"))
        elif now - peer.last_seen_event >= self.policy.seen_every_s:
            peer.last_seen_event = now
            events.append(self._event("seen", peer, now, "still heard"))
        peer.samples.append((now, sighting.rssi))
        peer.address, peer.flags, peer.last_heard = sighting.address, sighting.flags, now
        peer.true_m = sighting.true_m
        if self._paused_since is None:
            events += self._judge(peer, now)
        return events

    def tick(self, now: float) -> list[ProximityEvent]:
        """Let time pass: the robots that came near or went far since, and those long gone
        forgotten. Nothing changes while the tracker is suspended."""
        if self._paused_since is not None:
            return []
        events: list[ProximityEvent] = []
        for peer in list(self._peers.values()):
            events += self._judge(peer, now)
            gone = now - peer.last_heard > 2 * self.policy.stale_s
            if gone and not peer.near and not self._held(peer.beacon_id):
                del self._peers[peer.beacon_id]
        return events

    # ── links hold a robot near ──────────────────────────────────────────────────────────

    def hold(self, beacon_id: str, now: float) -> None:
        """A link to `beacon_id` opened: while it is open the robot is not far for want of
        adverts, since a robot busy on a link may advertise less, or a controller scan less.
        Quiet adverts still make it far."""
        self._holds[beacon_id] = self._holds.get(beacon_id, 0) + 1

    def release(self, beacon_id: str, now: float, *, lost: bool) -> list[ProximityEvent]:
        """A link to `beacon_id` closed. One that was lost, rather than closed, means the robot
        went out of range: it is far at once."""
        held = self._holds.get(beacon_id, 0)
        if held > 1:
            self._holds[beacon_id] = held - 1
        else:
            self._holds.pop(beacon_id, None)
        peer = self._peers.get(beacon_id)
        if peer is None:
            return []
        if lost and peer.near:
            return [self._far(peer, now, "link lost")]
        return []

    # ── a radio that stops listening for a while ─────────────────────────────────────────

    def suspend(self, now: float) -> None:
        """Stop the clock for every robot: the radio is not listening (BlueZ pauses scanning to
        connect, or the adapter went away), and silence it caused is not a robot gone."""
        if self._paused_since is None:
            self._paused_since = now

    def resume(self, now: float) -> None:
        """Start the clock again, as if no time had passed while it was stopped: everything
        from before the pause moves on by its length, and an advert heard during it, when the
        radio came back before the tracker was told, keeps the time it was heard."""
        since = self._paused_since
        if since is None:
            return
        gap = max(0.0, now - since)
        self._paused_since = None

        def moved(t: float) -> float:
            return t + gap if t <= since else t

        for peer in self._peers.values():
            peer.last_heard = moved(peer.last_heard)
            peer.last_seen_event = moved(peer.last_seen_event)
            peer.samples = deque(
                ((moved(t), r) for t, r in peer.samples), maxlen=peer.samples.maxlen
            )
            if peer.above_since is not None:
                peer.above_since = moved(peer.above_since)
            if peer.below_since is not None:
                peer.below_since = moved(peer.below_since)

    @property
    def suspended(self) -> bool:
        return self._paused_since is not None

    # ── what others ask ──────────────────────────────────────────────────────────────────

    def heard(self, beacon_id: str, *, within_s: float, now: float) -> bool:
        """Whether an advert with `beacon_id` was heard in the last `within_s` seconds, or a
        link to it is open: what a robot asks before it answers a hello that came over its LAN
        claiming that beacon id, since a robot it has not heard by radio is not one nearby."""
        if self._held(beacon_id):
            return True
        peer = self._peers.get(beacon_id)
        return peer is not None and now - peer.last_heard <= within_s

    def is_near(self, beacon_id: str) -> bool:
        peer = self._peers.get(beacon_id)
        return peer is not None and peer.near

    def peers(self, now: float) -> list[PeerView]:
        """Every robot heard and not yet forgotten, the nearest first."""
        views = []
        for peer in self._peers.values():
            rssi = self._median(peer, now)
            views.append(
                PeerView(
                    peer.beacon_id,
                    peer.address,
                    peer.flags,
                    peer.near,
                    rssi,
                    None if rssi is None else round(self.policy.metres(rssi), 2),
                    round(max(0.0, now - peer.last_heard), 2),
                    self._held(peer.beacon_id),
                    peer.true_m,
                )
            )
        return sorted(views, key=lambda v: -(v.rssi if v.rssi is not None else -math.inf))

    # ── the rules ────────────────────────────────────────────────────────────────────────

    def _median(self, peer: _Peer, now: float) -> float | None:
        """The median of what was heard in the window, or of the newest `min_samples` when the
        window holds fewer: a robot heard rarely is judged on its last few adverts."""
        if not peer.samples:
            return None
        recent = [r for t, r in peer.samples if now - t <= self.policy.window_s]
        if len(recent) < self.policy.min_samples:
            recent = [r for _t, r in list(peer.samples)[-self.policy.min_samples :]]
        return float(statistics.median(recent))

    def _judge(self, peer: _Peer, now: float) -> list[ProximityEvent]:
        policy = self.policy
        unheard = now - peer.last_heard
        if peer.near and unheard > policy.stale_s and not self._held(peer.beacon_id):
            return [self._far(peer, now, f"stale: unheard for {unheard:.1f} s")]
        median = self._median(peer, now)
        if median is None:
            return []
        if not peer.near:
            peer.below_since = None
            if median >= policy.near_dbm:
                if peer.above_since is None:
                    peer.above_since = now
                held = now - peer.above_since
                # and never on fewer adverts than a median needs, however loud the first was
                if (
                    held + _EPSILON >= policy.near_hold_s
                    and len(peer.samples) >= policy.min_samples
                ):
                    peer.near, peer.above_since = True, None
                    reason = f"{median:.1f} >= {policy.near_dbm:.1f} dBm for {held:.1f} s"
                    return [self._event("near", peer, now, reason)]
            else:
                peer.above_since = None
            return []
        peer.above_since = None
        if median <= policy.far_dbm:
            if peer.below_since is None:
                peer.below_since = now
            held = now - peer.below_since
            if held + _EPSILON >= policy.far_hold_s:
                reason = f"weak: {median:.1f} <= {policy.far_dbm:.1f} dBm for {held:.1f} s"
                return [self._far(peer, now, reason)]
        else:
            peer.below_since = None
        return []

    def _held(self, beacon_id: str) -> bool:
        return self._holds.get(beacon_id, 0) > 0

    def _far(self, peer: _Peer, now: float, reason: str) -> ProximityEvent:
        peer.near, peer.above_since, peer.below_since = False, None, None
        return self._event("far", peer, now, reason)

    def _event(
        self, kind: Literal["seen", "near", "far"], peer: _Peer, now: float, reason: str
    ) -> ProximityEvent:
        rssi = self._median(peer, now)
        metres = None if rssi is None else round(self.policy.metres(rssi), 2)
        return ProximityEvent(
            kind, peer.beacon_id, peer.address, peer.flags, rssi, metres, now, reason
        )

    def _make_room(self) -> bool:
        """Room for one more robot: when the table is full, the one heard longest ago is
        forgotten, never one near or held: a radio that floods the air with made-up beacon ids
        quietly pushes out only its own. One loud enough to be near, which any sender can be,
        fills the table with robots near, and a robot heard after that is not tracked until
        they go. False when every robot in it is near or held."""
        if len(self._peers) < self.max_peers:
            return True
        idle = [p for p in self._peers.values() if not p.near and not self._held(p.beacon_id)]
        if not idle:
            return False
        del self._peers[min(idle, key=lambda p: p.last_heard).beacon_id]
        return True


__all__ = ["PeerView", "ProximityEvent", "ProximityPolicy", "Tracker"]
