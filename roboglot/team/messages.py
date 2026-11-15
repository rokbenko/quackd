"""What robots and the coordinator say to each other, typed and transcript-ready.

Six message kinds mirror the research report's wire sketches (TASK, BID, CLAIM, ROLE, HB,
RESULT); 0.4 adds HINT (an arena-frame target estimate) and VERDICT (the spotter's own
judgement of a kick) for heterogeneous teams, and a capability term on BID (ADR-0020); 0.9
adds TALK, which is one LLM pilot saying something to another and is the only kind a pilot
team sends at all (ADR-0034).
Every addition has a default, so a 0.3 team's messages read exactly as before.
Timestamps are sim time from the shared clock, so a transcript replay lines up with the
world, not with wall-clock noise.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from roboglot.glotfile.schema import TeamRole


class Wedge(BaseModel):
    """A heading sector one duck owns during SEARCH, in absolute degrees."""

    model_config = ConfigDict(extra="forbid")

    start_deg: float
    end_deg: float

    @property
    def width_deg(self) -> float:
        return (self.end_deg - self.start_deg) % 360 or 360.0


class TeamTask(BaseModel):
    """The planner's output: the knobs the coordinator and the role FSMs run on."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    name: str
    goal: str
    target: str = Field(default="ball", description="Detector label to hunt.")
    kick_leg: Literal["left", "right"] = "right"
    stop_distance: float = Field(default=0.22, ge=0.1, le=1.0)
    step_deg: float = Field(default=45.0, ge=15, le=120)
    restart_s: float = Field(default=8.0, gt=0, le=120, description="Re-scan cadence.")
    timeout_s: float = Field(default=90.0, gt=0, le=600, description="Global cap, sim seconds.")
    max_search_rounds: int = Field(default=2, ge=1, le=10)
    success_moved_m: float = Field(default=0.3, gt=0, le=2.0)
    roles: dict[str, TeamRole] = Field(
        default_factory=dict, description="v1 roles; empty means the single-role auction."
    )
    frame_hints: bool = Field(default=False, description="Resolved by the runner (auto/on/off).")
    judge_margin_m: float = Field(
        default=0.15,
        ge=0,
        le=0.5,
        description="Added to success_moved_m for the spotter's verdict, because the "
        "size-based distance estimate quantises in ~0.2 m steps beyond 1.5 m. A stricter "
        "spotter costs a re-kick; a lenient one would let the world's veto fail the run.",
    )
    judge_timeout_s: float = Field(default=6.0, gt=0, le=60)


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")

    t: float = Field(description="Sim time at publish, from the shared clock.")
    src: str = Field(description="Member name or 'coordinator'.")
    task_id: str


class TaskMsg(_Base):
    kind: Literal["TASK"] = "TASK"
    task: TeamTask
    members: list[str]


class BidMsg(_Base):
    kind: Literal["BID"] = "BID"
    ball_dist_m: float = Field(description="The bidder's OWN camera estimate.")
    bearing_deg: float = 0.0
    confidence: float = 1.0
    role: str | None = Field(default=None, description="Which role this bid is for (v1).")
    provides: list[str] = Field(
        default_factory=list,
        description="The capability term: the bidder's canonical verb names from its manifest.",
    )
    datasheet: dict[str, Any] | None = Field(
        default=None,
        description="v2: the bidder's datasheet, so a coordinator can check a role's physical "
        "needs against what the robot says about itself rather than taking its word for it.",
    )
    mobility: str | None = Field(
        default=None, description="v2: the bidder's manifest mobility, for a role that needs one."
    )


class ClaimMsg(_Base):
    kind: Literal["CLAIM"] = "CLAIM"
    kicker: str
    lease_s: float = 6.0
    assignments: dict[str, str] = Field(default_factory=dict, description="role -> member (v1)")


class Hint(BaseModel):
    """Where a robot believes the target is, in the ARENA frame (sim only: on hardware no
    robot knows where another one is mounted)."""

    model_config = ConfigDict(extra="forbid")

    target: str
    frame: Literal["arena"] = "arena"
    x_m: float
    y_m: float
    by: str
    est_dist_m: float
    bearing_deg: float


class RoleMsg(_Base):
    kind: Literal["ROLE"] = "ROLE"
    duck: str
    role: Literal["SEARCH", "KICK", "YIELD", "STOP", "SPOT", "JUDGE"]
    wedge: Wedge | None = None
    min_sep_m: float = 0.4
    retreat: bool = Field(
        default=False,
        description="YIELD only: the coordinator measured you inside the separation ring "
        "(ground truth), back away now.",
    )
    team_role: str | None = Field(default=None, description="spotter / kicker / None (v1)")
    seq: int = Field(default=0, description="Coordinator counter: a repeated order is new.")
    hint: Hint | None = None
    kicker: str | None = Field(default=None, description="JUDGE only: whose kick to judge.")


class HintMsg(_Base):
    kind: Literal["HINT"] = "HINT"
    hint: Hint


class VerdictMsg(_Base):
    """The spotter's judgement from its own fresh frames. Never a claim by the actor."""

    kind: Literal["VERDICT"] = "VERDICT"
    target: str
    kicker: str
    verdict: Literal["moved", "not_moved", "lost"]
    moved_m: float | None = Field(
        default=None,
        description="Displacement since the spotter's FIRST sighting, its own estimate.",
    )
    ref: dict[str, float] = Field(default_factory=dict)
    seen: dict[str, float] | None = None
    frames: int = 0


class HbMsg(_Base):
    kind: Literal["HB"] = "HB"
    role: str
    posture: str = "standing"
    fallen: bool = False
    battery_percent: float | None = None
    x: float | None = None
    y: float | None = None
    steps: int = 0


class ResultMsg(_Base):
    kind: Literal["RESULT"] = "RESULT"
    status: Literal["kicked", "miss", "fell", "search_empty", "budget", "aborted", "kick_done"]
    """`kick_done` (v1 roles): "I kicked and stepped clear", a report and never a claim."""
    detail: str = ""
    ball_moved_m: float | None = None


TALK_MAX_CHARS = 400
"""What one pilot may say to another in one message. A pilot team pays for every word twice,
once in the sender's output and once in every receiver's next observation, and a sentence that
does not fit here was going to be a paragraph nobody read."""


class TalkMsg(_Base):
    """One pilot to another, or to everyone. The only kind a pilot team publishes.

    An auction never sends one: its members are state machines and have nothing to say. The
    runner itself sends one under `src="team"` when a member's loop ends, so the survivors
    hear it in their next observation rather than waiting for someone who has stopped."""

    kind: Literal["TALK"] = "TALK"
    to: str | None = Field(
        default=None, description="A member name, or None for everyone but the sender."
    )
    text: str = Field(..., min_length=1, max_length=TALK_MAX_CHARS)


TeamMessage = Annotated[
    TaskMsg | BidMsg | ClaimMsg | RoleMsg | HbMsg | ResultMsg | HintMsg | VerdictMsg | TalkMsg,
    Field(discriminator="kind"),
]
