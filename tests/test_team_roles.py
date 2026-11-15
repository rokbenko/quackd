"""Heterogeneous team primitives: the capability term, the role auction, the new messages."""

from __future__ import annotations

import random
from types import SimpleNamespace as NS

from pydantic import TypeAdapter

from roboglot.adapters.manifest import Datasheet, Figure, RobotManifest, VerbSpec
from roboglot.glotfile.schema import TeamRole
from roboglot.team.auction import AuctionPolicy, RoleAuction
from roboglot.team.capability import eligible_roles, missing, missing_needs, missing_needs_in
from roboglot.team.messages import (
    BidMsg,
    Hint,
    HintMsg,
    ResultMsg,
    RoleMsg,
    TeamMessage,
    VerdictMsg,
)
from roboglot.verdict import NEEDS_NUMBERS, NEEDS_WORDS

ROLES = {
    "spotter": TeamRole(requires=["observe", "gaze"]),
    "kicker": TeamRole(requires=["go_to", "kick"]),
}
DUCK = ["observe", "report_state", "stop", "say", "move", "go_to", "search_scan", "kick", "gaze"]
HEAD = ["observe", "report_state", "stop", "say", "search_scan", "gaze", "express"]


def test_capability_term_is_alias_aware() -> None:
    assert missing(["get_frame", "walk_to"], DUCK) == []
    assert missing(["go_to", "kick"], HEAD) == ["go_to", "kick"]
    assert eligible_roles(ROLES, DUCK) == ["kicker", "spotter"]  # a duck can spot too
    assert eligible_roles(ROLES, HEAD) == ["spotter"]
    assert eligible_roles(ROLES, ["stop"]) == []


def _bid(src: str, role: str, dist: float, t: float = 0.0) -> BidMsg:
    return BidMsg(t=t, src=src, task_id="t", ball_dist_m=dist, role=role, provides=[])


def test_role_auction_fills_most_constrained_role_first() -> None:
    now = NS(t=0.0)
    auction = RoleAuction(AuctionPolicy(window_s=0.4), lambda: now.t, ROLES)
    auction.open(_bid("spotter-01", "spotter", 1.1))
    assert not auction.complete(set())  # nobody bid for kicker yet
    auction.add(_bid("duck-01", "spotter", 0.78))  # the duck is closer, and could spot...
    auction.add(_bid("duck-01", "kicker", 0.78))  # ...but it is the only one who can kick
    assert auction.complete(set())
    now.t = 0.5
    assert auction.due()
    decision = auction.decide({}, set())
    assert decision is not None
    assert decision.assignments == {"kicker": "duck-01", "spotter": "spotter-01"}
    assert decision.kicker == "duck-01" and decision.costs["spotter"] == 1.1
    assert decision.ties == [] and decision.hysteresis_applied == []


def test_role_auction_is_independent_of_bid_order() -> None:
    bids = [
        _bid("duck-01", "kicker", 0.9),
        _bid("duck-02", "kicker", 0.7),
        _bid("duck-01", "spotter", 0.9),
        _bid("duck-02", "spotter", 0.7),
        _bid("spotter-01", "spotter", 1.3),
    ]
    outcomes = set()
    for seed in range(20):
        shuffled = list(bids)
        random.Random(seed).shuffle(shuffled)
        auction = RoleAuction(AuctionPolicy(), lambda: 0.0, ROLES)
        auction.open(shuffled[0])
        for b in shuffled[1:]:
            auction.add(b)
        decision = auction.decide({}, set())
        assert decision is not None
        outcomes.add(tuple(sorted(decision.assignments.items())))
    assert outcomes == {(("kicker", "duck-02"), ("spotter", "duck-01"))}


def test_role_auction_holds_the_spotter_and_applies_hysteresis_per_role() -> None:
    auction = RoleAuction(AuctionPolicy(hysteresis=0.2), lambda: 0.0, ROLES)
    auction.held = {"spotter": "spotter-01"}
    auction.open(_bid("duck-01", "kicker", 1.0))  # the previous kicker
    auction.add(_bid("duck-02", "kicker", 0.85))  # 15 % better: not enough
    auction.add(_bid("spotter-01", "spotter", 0.5))  # ignored: the role is held
    decision = auction.decide({"kicker": "duck-01"}, set())
    assert decision is not None
    assert decision.assignments == {"spotter": "spotter-01", "kicker": "duck-01"}
    assert decision.hysteresis_applied == ["kicker"]
    auction.open(_bid("duck-02", "kicker", 0.7))  # 30 % better: unseats
    auction.add(_bid("duck-01", "kicker", 1.0))
    decision = auction.decide({"kicker": "duck-01"}, set())
    assert decision is not None and decision.assignments["kicker"] == "duck-02"


def test_role_auction_is_void_when_a_role_cannot_be_filled() -> None:
    auction = RoleAuction(AuctionPolicy(), lambda: 0.0, ROLES)
    auction.open(_bid("duck-01", "kicker", 0.5))
    auction.add(_bid("duck-01", "spotter", 0.5))  # one duck cannot take both roles
    assert not auction.complete(set())
    assert auction.decide({}, set()) is None
    auction.open(_bid("duck-01", "kicker", 0.5))
    auction.add(_bid("spotter-01", "spotter", 1.0))
    assert auction.decide({}, {"spotter-01"}) is None  # the only spotter is excluded
    auction.open(_bid("duck-01", "dancer", 0.5))  # an unknown role is dropped on add
    assert auction.bids == {}


def test_new_messages_round_trip_and_old_ones_keep_their_defaults() -> None:
    adapter: TypeAdapter[TeamMessage] = TypeAdapter(TeamMessage)
    hint = Hint(target="ball", x_m=0.2, y_m=-0.4, by="spotter-01", est_dist_m=0.9, bearing_deg=12)
    for msg in (
        HintMsg(t=1.0, src="spotter-01", task_id="t", hint=hint),
        VerdictMsg(
            t=2.0,
            src="spotter-01",
            task_id="t",
            target="ball",
            kicker="duck-01",
            verdict="moved",
            moved_m=0.61,
            ref={"x_m": 0.2, "y_m": -0.4},
            seen={"x_m": 0.8, "y_m": -0.3},
            frames=2,
        ),
        RoleMsg(
            t=3.0,
            src="coordinator",
            task_id="t",
            duck="spotter-01",
            role="JUDGE",
            kicker="duck-01",
            seq=5,
        ),
        ResultMsg(t=4.0, src="duck-01", task_id="t", status="kick_done", ball_moved_m=0.6),
    ):
        again = adapter.validate_python(msg.model_dump())
        assert again == msg and again.kind == msg.kind
    legacy = adapter.validate_python(
        {"kind": "BID", "t": 0.0, "src": "duck-1", "task_id": "t", "ball_dist_m": 0.6}
    )
    assert isinstance(legacy, BidMsg) and legacy.role is None and legacy.provides == []
    role = adapter.validate_python(
        {
            "kind": "ROLE",
            "t": 0.0,
            "src": "coordinator",
            "task_id": "t",
            "duck": "duck-1",
            "role": "KICK",
        }
    )
    assert isinstance(role, RoleMsg) and role.seq == 0 and role.team_role is None


# ── v2: a role that asks for a body, not only a vocabulary ──────────────────────────────

CARRY = {
    "spotter": TeamRole(requires=["observe", "gaze"]),
    "kicker": TeamRole(requires=["go_to", "kick"], needs={"payload_kg": 1.0}),
}


def _sheet(**over: object) -> Datasheet:
    base: dict[str, object] = {"manipulator": "gripper", "arms": 1}
    base.update(over)
    return Datasheet(**base)  # type: ignore[arg-type]


def _robot(sheet: Datasheet | None, mobility: str = "legged") -> RobotManifest:
    return RobotManifest(
        id="bot-01",
        vendor="acme",
        model="bot",
        embodiment="biped",
        mobility=mobility,  # type: ignore[arg-type]
        intents=["twist"],
        verbs=[VerbSpec(name="move", core=True)],
        datasheet=sheet,
    )


def test_the_needs_vocabulary_is_the_datasheets_own() -> None:
    """A role and a pilot's refusal use the same words, so they cannot mean different things."""
    for key in NEEDS_NUMBERS:
        assert key in Datasheet.model_fields or key == "work_height_m", key
    assert set(NEEDS_WORDS) == {"manipulator", "mobility", "terrain"}


def test_a_role_can_ask_for_a_body_and_an_unknown_figure_is_not_a_yes() -> None:
    strong = _robot(_sheet(payload_kg=Figure(value=1.0, confidence="official", source="the docs")))
    light = _robot(_sheet(payload_kg=Figure(value=0.5, confidence="official", source="the docs")))
    silent = _robot(_sheet())

    assert eligible_roles(CARRY, DUCK, strong) == ["kicker", "spotter"]
    assert eligible_roles(CARRY, DUCK, light) == ["spotter"]
    assert eligible_roles(CARRY, DUCK, silent) == ["spotter"], "unknown is not a yes"
    # and with nobody to ask about the body at all
    assert eligible_roles(CARRY, DUCK) == ["spotter"]
    # a v1 role is unaffected, manifest or no manifest
    assert eligible_roles(ROLES, DUCK) == ["kicker", "spotter"]

    assert missing_needs({"payload_kg": 1.0}, light) == ["payload_kg >= 1 (has 0.5)"]
    assert missing_needs({"payload_kg": 1.0}, silent) == ["payload_kg >= 1 (not published)"]
    assert missing_needs({"payload_kg": 1.0}, strong) == []


def test_a_bid_carries_the_facts_a_coordinator_judges_it_by() -> None:
    adapter: TypeAdapter[TeamMessage] = TypeAdapter(TeamMessage)
    sheet = _sheet(payload_kg=Figure(value=1.0, confidence="official", source="the docs"))
    bid = BidMsg(
        t=1.0,
        src="cart",
        task_id="t",
        ball_dist_m=0.9,
        role="kicker",
        provides=DUCK,
        datasheet=sheet.model_dump(mode="json"),
        mobility="wheeled",
    )
    again = adapter.validate_python(bid.model_dump())
    assert again == bid
    assert isinstance(again, BidMsg) and again.datasheet is not None
    assert again.datasheet["payload_kg"]["value"] == 1.0
    legacy = adapter.validate_python(
        {"kind": "BID", "t": 0.0, "src": "duck-1", "task_id": "t", "ball_dist_m": 0.6}
    )
    assert isinstance(legacy, BidMsg) and legacy.datasheet is None and legacy.mobility is None


def test_missing_needs_reads_a_bids_own_words() -> None:
    """The coordinator never sees the bidder's manifest, only what the bid carried."""
    sheet = _sheet(payload_kg=Figure(value=0.5, confidence="official", source="the docs"))
    facts = sheet.model_dump()
    assert missing_needs_in({"payload_kg": 1.0}, facts, "wheeled") == ["payload_kg >= 1 (has 0.5)"]
    assert missing_needs_in({"payload_kg": 0.5}, facts, "wheeled") == []
    assert missing_needs_in({"payload_kg": 1.0}, {}, None) == ["payload_kg >= 1 (not published)"]
    assert missing_needs_in({"mobility": "wheeled"}, facts, "legged") == [
        "mobility = wheeled (has legged)"
    ]


def test_a_body_that_does_not_move_now_fills_a_role_on_a_flat_indoor_floor() -> None:
    """A behaviour change, on purpose. A role asking `terrain: indoor_flat` used to refuse a
    body that does not move and publishes no terrain, as "(not published)", because the
    exception for an unpublished terrain stopped at bodies that move. The pilot of such a body
    is shown "it does not move" where a moving one is shown its terrain, and was refused the
    same way for naming the floor its table stands on. The reader is shared, so the fix reaches
    a team too: such a body now meets `indoor_flat` at the member before it bids and at the
    coordinator judging the bid, and a role asking for more is refused as "(it does not
    move)". A bid that carried no datasheet still qualifies for nothing.

    A role's working height stays strict. The kinder reading of an unpublished working height
    is for a pilot judging its own body, which the prompt never showed the gap; a coordinator
    offering a task at a height to a body that never said how high it works is guessing."""
    sheet = _sheet(tethered=True)
    still = RobotManifest(
        id="arm-01",
        vendor="acme",
        model="arm",
        embodiment="arm",
        mobility="none",
        intents=["joint"],
        verbs=[VerbSpec(name="observe"), VerbSpec(name="stop", core=True)],
        datasheet=sheet,
    )
    roles = {
        "spotter": TeamRole(requires=["observe"], needs={"terrain": "indoor_flat"}),
        "kicker": TeamRole(requires=["observe"], needs={"terrain": "indoor"}),
    }
    assert eligible_roles(roles, ["observe"], still) == ["spotter"]

    facts = sheet.model_dump(mode="json")
    assert missing_needs_in({"terrain": "indoor_flat"}, facts, "none") == []
    assert missing_needs_in({"terrain": "indoor"}, facts, "none") == [
        "terrain = indoor (it does not move)"
    ]
    assert missing_needs_in({"terrain": "indoor_flat"}, {}, "none") == [
        "terrain = indoor_flat (not published)"
    ], "a bid with no datasheet said nothing"

    # `none` is a need a role can name, and a body with legs meets it as well as one without
    idle = {"spotter": TeamRole(requires=["observe"], needs={"mobility": "none"})}
    walker = _robot(_sheet(), mobility="legged")
    assert eligible_roles(idle, ["observe"], still) == ["spotter"]
    assert eligible_roles(idle, ["observe"], walker) == ["spotter"]

    for height in (0.2, 0.75):
        assert missing_needs_in({"work_height_m": height}, facts, "none") == [
            f"work_height_m = {height:g} (not published)"
        ]
        assert missing_needs({"work_height_m": height}, still) != []
