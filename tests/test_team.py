"""Team primitives: the contract block, messages, the bus and the transcript."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from roboglot.glotfile.parser import GlotParseError, parse_glot_text
from roboglot.glotfile.schema import COORDINATOR_GONE, PILOTS_MAX_MEMBERS, TeamSection
from roboglot.log import LogEvent
from roboglot.team.bus import InProcessBus
from roboglot.team.messages import TalkMsg, TeamMessage
from roboglot.team.transcript import TeamTranscript

# ── schema ──────────────────────────────────────────────────────────────────────────────


def test_team_section_defaults_and_names() -> None:
    team = TeamSection()
    assert team.member_names == ["duck-0", "duck-1", "duck-2"]
    assert team.allocation.method == "pilots"
    named = TeamSection(members=["ada", "grace"])
    assert named.member_names == ["ada", "grace"]


@pytest.mark.parametrize(
    "bad", [1, PILOTS_MAX_MEMBERS + 1, ["solo"], ["a", "a"], ["A", "b"], ["x"] * 5]
)
def test_team_members_validation(bad: Any) -> None:
    with pytest.raises(ValidationError):
        TeamSection(members=bad)


def test_pilots_take_up_to_eight_members() -> None:
    """The bound is what a person can read in one terminal."""
    eight = [f"duck-{i}" for i in range(PILOTS_MAX_MEMBERS)]
    assert len(TeamSection(members=eight, allocation={"method": "pilots"}).member_names) == 8
    with pytest.raises(ValidationError, match="2 to 8"):
        TeamSection(members=[*eight, "duck-8"], allocation={"method": "pilots"})


@pytest.mark.parametrize(
    ("setting", "named"),
    [
        ({"roles": {"spotter": {"requires": ["observe"]}}}, "team.roles"),
        ({"safety": {"one_claimant": True}}, "team.safety"),
        ({"search": {"restart_s": 30}}, "team.search"),
        ({"frame_hints": "auto"}, "team.frame_hints"),
        ({"allocation": {"bid": "ball_dist"}}, "team.allocation.bid"),
        ({"allocation": {"tie_break": "name"}}, "team.allocation.tie_break"),
        ({"allocation": {"hysteresis_pct": 20}}, "team.allocation.hysteresis_pct"),
        ({"allocation": {"claim_lease_s": 2}}, "team.allocation.claim_lease_s"),
        ({"allocation": {"method": "auction"}}, "team.allocation.method: auction"),
    ],
)
def test_a_coordinator_setting_is_refused_by_name(setting: dict[str, Any], named: str) -> None:
    with pytest.raises(ValidationError) as refused:
        TeamSection.model_validate({"members": ["ada", "grace"], **setting})
    assert f"{named}: {COORDINATOR_GONE}" in str(refused.value), str(refused.value)


def test_a_team_needs_glot_1() -> None:
    text = (
        "---\nglot: {v}\nname: t\ndescription: d\nverbs:\n"
        "  allow: [stop]\nsuccess: [x]\nteam:\n  members: [ada, grace]\n"
        "  allocation:\n    method: pilots\n---\n# T\nx\n"
    )
    with pytest.raises(GlotParseError, match="team needs glot: 1"):
        parse_glot_text(text.format(v=0))
    assert parse_glot_text(text.format(v=1)).frontmatter.team is not None


def test_team_block_parses_in_a_task_file_and_is_optional() -> None:
    glot = parse_glot_text(
        "---\nglot: 1\nname: t\ndescription: d\nverbs:\n  allow: [stop]\nsuccess: [x]\n"
        "team:\n  members: 2\n---\n# T\nx\n"
    )
    assert glot.frontmatter.team is not None
    assert glot.frontmatter.team.member_names == ["duck-0", "duck-1"]
    solo = parse_glot_text(
        "---\nglot: 0\nname: t\ndescription: d\nverbs:\n  allow: [stop]\n"
        "success: [x]\n---\n# T\nx\n"
    )
    assert solo.frontmatter.team is None


# ── messages ────────────────────────────────────────────────────────────────────────────


def test_messages_round_trip_through_json() -> None:
    adapter: TypeAdapter[TeamMessage] = TypeAdapter(TeamMessage)
    talk = TalkMsg(t=1.5, src="duck-1", task_id="t1", to="duck-0", text="the ball is by the door")
    again = adapter.validate_python(talk.model_dump())
    assert again == talk and again.kind == "TALK"


# ── bus ─────────────────────────────────────────────────────────────────────────────────


def test_bus_fanout_no_echo_and_tap() -> None:
    tapped: list[TeamMessage] = []
    bus = InProcessBus(tap=tapped.append)
    a = bus.subscribe("duck-0")
    b = bus.subscribe("duck-1")
    msg = TalkMsg(t=0.0, src="duck-0", task_id="t", text="I see the ball")
    bus.publish(msg)
    assert b.drain() == [msg]
    assert a.drain() == []  # no echo to the sender
    assert tapped == [msg] and bus.published == 1
    b.close()
    bus.publish(msg)
    assert b.drain() == []  # closed subscriptions receive nothing


# ── the log ─────────────────────────────────────────────────────────────────────────────


def test_the_team_transcript_is_a_log_record_stamped_on_the_teams_clock(tmp_path: Path) -> None:
    transcript = TeamTranscript(tmp_path, now=lambda: 4.2)
    transcript.sink(LogEvent("llm", 99.0, {"provider": "stub", "usage": {"input_tokens": 3}}))
    transcript.close()
    lines = (tmp_path / "team.jsonl").read_text(encoding="utf-8").splitlines()
    # the event's own clock (99.0) is dropped: team.jsonl is stamped on the team's clock only
    assert [json.loads(line) for line in lines if line.strip()] == [
        {"t": 4.2, "kind": "llm", "provider": "stub", "usage": {"input_tokens": 3}}
    ]
