"""What the pilots in a team say to each other, typed and transcript-ready.

One kind, TALK: one LLM pilot saying something to another (ADR-0034). Timestamps are seconds
on the team's own clock at publish, which is the wall clock.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")

    t: float = Field(description="Seconds on the team's clock at publish.")
    src: str = Field(description="Member name, or 'team' for the runner itself.")
    task_id: str


TALK_MAX_CHARS = 400
"""What one pilot may say to another in one message. A pilot team pays for every word twice,
once in the sender's output and once in every receiver's next observation, and a sentence that
does not fit here was going to be a paragraph nobody read."""


class TalkMsg(_Base):
    """One pilot to another, or to everyone, and the only kind there is.

    The runner itself sends one under `src="team"` when a member's loop ends, so the survivors
    hear it in their next observation rather than waiting for someone who has stopped."""

    kind: Literal["TALK"] = "TALK"
    to: str | None = Field(
        default=None, description="A member name, or None for everyone but the sender."
    )
    text: str = Field(..., min_length=1, max_length=TALK_MAX_CHARS)


TeamMessage = TalkMsg
"""What crosses a team bus: a pilot's TALK, typed, which the MQTT wire carries unchanged."""
