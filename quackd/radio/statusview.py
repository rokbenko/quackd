"""`quackd onboard status`, the radio's half: what the radio adds to status.json, and the words.

The robot's service writes status.json every two seconds and `status` reads it from another
process, so the radio's part of it is plain JSON, made from the live objects in one place
(`radio_snapshot`), and turned into lines a person reads in another (`render_radio`): the radio's
state and what would fix it, the beacon id and when it rotates, how many adverts a second are
heard, what near means in decibels, the address privacy as far as it can be seen, and every
robot heard, near or far, with what the meeting planner made of it.

The thresholds come from a model nobody has calibrated, so the words say so; an estimate of
metres is written with a tilde, and the sim's own distances, which no real radio has, are marked
as the sim's.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from quackd.radio import RadioStatus
from quackd.radio.bluez.privacy import privacy_setting, privacy_words
from quackd.radio.meet import Planner
from quackd.radio.proximity import Tracker
from quackd.radio.rotation import BeaconRotor

#: How many robots the snapshot lists, nearest first: a status is read by a person.
PEERS_SHOWN = 16


class Rate:
    """Adverts heard a second, from a running count: smoothed over about ten seconds, so one
    busy second does not read as a crowd."""

    def __init__(self, smoothing_s: float = 10.0) -> None:
        self.smoothing_s = smoothing_s
        self._last: tuple[int, float] | None = None
        self.per_s: float | None = None

    def update(self, count: int, now: float) -> float | None:
        if self._last is not None:
            seen, then = self._last
            dt = now - then
            if dt > 0:
                instant = max(0, count - seen) / dt
                weight = min(1.0, dt / self.smoothing_s)
                self.per_s = (
                    instant if self.per_s is None else self.per_s + weight * (instant - self.per_s)
                )
        self._last = (count, now)
        return self.per_s


def radio_snapshot(
    *,
    status: RadioStatus,
    now: float,
    rotor: BeaconRotor | None = None,
    tracker: Tracker | None = None,
    planner: Planner | None = None,
    heard_per_s: float | None = None,
    main_conf: str | None = None,
) -> dict[str, Any]:
    """The radio's part of status.json, all of it JSON: its status, the beacon and rotation,
    the near and far thresholds, the privacy words, and the robots heard."""
    snapshot: dict[str, Any] = {
        "kind": status.kind,
        "state": status.state,
        "detail": status.detail,
        "hint": status.hint,
        "adapter": status.adapter,
        "address": status.address,
        "address_type": status.address_type,
        "advertising": status.advertising,
        "scanning": status.scanning,
        "accepting": status.accepting,
        "links": status.links,
        "heard_per_s": None if heard_per_s is None else round(heard_per_s, 1),
    }
    if rotor is not None:
        snapshot["beacon"] = {
            "id": rotor.current,
            "rotates_in_s": round(rotor.due_in(now), 1),
            "rotations": rotor.rotations,
            "deferred": rotor.deferred,
        }
    if tracker is not None:
        policy = tracker.policy
        snapshot["near"] = {
            "within_m": policy.meet_within_m,
            "near_dbm": round(policy.near_dbm, 1),
            "far_dbm": round(policy.far_dbm, 1),
            "rssi_at_1m": policy.rssi_at_1m,
            "path_loss_n": policy.path_loss_n,
        }
    if status.kind == "bluez":
        setting = privacy_setting(main_conf) if main_conf is not None else None
        private, words = privacy_words(setting, status.address_type)
        snapshot["privacy"] = {"setting": setting, "private": private, "words": words}
    plans = {p.beacon_id: p for p in planner.peers()} if planner is not None else {}
    peers = []
    for view in tracker.peers(now) if tracker is not None else []:
        plan = plans.pop(view.beacon_id, None)
        peers.append(
            {
                "beacon_id": view.beacon_id,
                "near": view.near,
                "rssi": view.rssi,
                "metres": view.metres,
                "heard_s_ago": view.heard_s_ago,
                "held": view.held,
                "true_m": view.true_m,
                "plan": plan.state if plan else None,
                "via": plan.via if plan else None,
                "did": plan.did if plan else None,
                "reason": plan.reason if plan else None,
            }
        )
    for plan in plans.values():  # met, or meeting, and no longer heard
        peers.append(
            {
                "beacon_id": plan.beacon_id,
                "near": plan.near,
                "rssi": None,
                "metres": None,
                "heard_s_ago": None,
                "held": plan.inbound > 0,
                "true_m": None,
                "plan": plan.state,
                "via": plan.via,
                "did": plan.did,
                "reason": plan.reason,
            }
        )
    snapshot["peers"] = peers[:PEERS_SHOWN]
    snapshot["peers_more"] = max(0, len(peers) - PEERS_SHOWN)
    return snapshot


def duration(seconds: float | None) -> str:
    """Seconds as a person says them: 45 s, 8 min 32 s, 2 h 5 min."""
    if seconds is None or not math.isfinite(seconds):
        return "unknown"
    whole = round(max(0.0, seconds))
    if whole < 60:
        return f"{whole} s"
    minutes, secs = divmod(whole, 60)
    if minutes < 60:
        return f"{minutes} min {secs} s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min"


def _what_it_does(radio: Mapping[str, Any]) -> str:
    doing = [
        name
        for name, on in (
            ("advertising", radio.get("advertising")),
            ("scanning", radio.get("scanning")),
            ("taking links", radio.get("accepting")),
        )
        if on
    ]
    rate = radio.get("heard_per_s")
    if radio.get("scanning") and rate is not None:
        doing[doing.index("scanning")] = f"scanning ({rate:g} passport adverts a second)"
    links = radio.get("links") or 0
    said = ", ".join(doing) if doing else "nothing"
    return f"{said}; {links} link{'s' if links != 1 else ''} open"


def _peer_line(peer: Mapping[str, Any]) -> str:
    beacon = str(peer.get("beacon_id", "?"))
    where = "near" if peer.get("near") else "far"
    parts = [f"{beacon}  {where:<4}"]
    if peer.get("rssi") is not None:
        parts.append(f"{peer['rssi']:.1f} dBm")
    if peer.get("metres") is not None:
        parts.append(f"~{peer['metres']:g} m")
    if peer.get("true_m") is not None:
        parts.append(f"(the sim puts it at {peer['true_m']:g} m)")
    plan, via = peer.get("plan"), peer.get("via")
    if plan == "met":
        parts.append(f"met over {'the LAN' if via == 'lan' else 'a link'}")
    elif plan:
        reason = peer.get("reason")
        parts.append(f"{plan}: {reason}" if reason and plan != "meeting" else str(plan))
    elif peer.get("heard_s_ago") is not None:
        parts.append(f"heard {duration(peer['heard_s_ago'])} ago")
    if peer.get("did"):
        parts.append(str(peer["did"]))
    return "  ".join(parts)


def render_radio(radio: Mapping[str, Any]) -> list[str]:
    """The radio's part of status.json as lines a person reads."""
    state = radio.get("state", "unknown")
    where = radio.get("adapter") or radio.get("kind") or "radio"
    lines = [
        f"radio     {radio.get('kind', '?')} {where}, {state}: {radio.get('detail') or ''}".rstrip()
    ]
    if radio.get("hint") and state != "ready":
        lines.append(f"          -> {radio['hint']}")
    lines.append(f"          {_what_it_does(radio)}")
    beacon = radio.get("beacon")
    if beacon:
        rotated = beacon.get("rotations", 0)
        deferred = beacon.get("deferred", 0)
        due = duration(beacon.get("rotates_in_s"))
        times = f"{rotated} time{'s' if rotated != 1 else ''}"
        put_off = f", put off {deferred} times for a meeting" if deferred else ""
        lines.append(
            f"beacon    {beacon.get('id')} (next rotation in {due}; rotated {times}{put_off})"
        )
    near = radio.get("near")
    if near:
        model = f"{near['rssi_at_1m']:g} dBm at 1 m and n {near['path_loss_n']:g}"
        lines.append(
            f"near      within {near['within_m']:g} m: {near['near_dbm']:g} dBm or louder is "
            f"near, {near['far_dbm']:g} dBm or quieter far; uncalibrated, from {model}"
        )
    privacy = radio.get("privacy")
    if privacy:
        lines.append(f"privacy   {privacy.get('words')}")
    peers: Sequence[Mapping[str, Any]] = radio.get("peers") or []
    if not peers:
        lines.append("robots    none heard")
    for i, peer in enumerate(peers):
        lines.append(f"{'robots' if i == 0 else '':<10}{_peer_line(peer)}")
    if radio.get("peers_more"):
        lines.append(f"          and {radio['peers_more']} more")
    return lines


def render_status(status: Mapping[str, Any]) -> list[str]:
    """A whole status.json as lines: each section the service wrote, as `key  value` lines,
    with the radio's in its own words. A section this does not know is shown as it is."""
    lines: list[str] = []
    for section, value in status.items():
        if section == "radio" and isinstance(value, Mapping):
            lines += render_radio(value)
        elif isinstance(value, Mapping):
            shown = ", ".join(f"{k} {v}" for k, v in value.items() if v not in (None, "", [], {}))
            lines.append(f"{section:<9} {shown}")
        elif isinstance(value, list):
            lines.append(f"{section:<9} {len(value)}")
            lines += [f"{'':<10}{item}" for item in value[-5:]]
        elif value not in (None, ""):
            lines.append(f"{section:<9} {value}")
    return lines


__all__ = [
    "PEERS_SHOWN",
    "Rate",
    "duration",
    "radio_snapshot",
    "render_radio",
    "render_status",
]
