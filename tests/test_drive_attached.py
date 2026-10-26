"""A goal borrows the body a drive session holds, and leaves it as it found it."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from quackd.adapters.base import HandResult, RestResult
from quackd.agent.loop import AgentLoop, RunConfig
from quackd.agent.providers.fake import FakeProvider
from quackd.agent.transcript import Transcript
from quackd.drive.attached import AttachedBody
from quackd.duckfile.schema import DuckFile
from quackd.transport.mock import MockTransport
from quackd_microduck import microduck_manifest
from quackd_microduck.verbs import MICRODUCK_VERBS, microduck_conditions

NEWER = "a newer command, sit"


class _Held(MockTransport):
    """A body the session has connected and will fold and let go of when it closes, which counts
    everything a goal could do to it that only the session may, and can be told to have the
    session move on from the goal once the goal's first walk has ended."""

    rest_pose = {"head": 0.0}
    close_note = "torque released on every motor"
    connect_notes = ("the bus lost a packet and connected on the second try",)

    def __init__(self) -> None:
        super().__init__()
        self.connected = True
        self.done: list[str] = []
        self.loop: AgentLoop | None = None

    async def connect(self) -> None:
        self.done.append("connect")
        await super().connect()

    async def close(self) -> None:
        self.done.append("close")
        await super().close()

    async def go_to_rest(self) -> RestResult:
        self.done.append("go_to_rest")
        return RestResult("reached", "")

    async def let_go(self, **_kw: Any) -> HandResult:
        self.done.append("let_go")
        return HandResult("released", "")

    async def take_hold(self) -> HandResult:
        self.done.append("take_hold")
        return HandResult("held", "")

    def set_stop_check(self, check: Any) -> None:
        self.done.append("set_stop_check")

    # the Microduck's own verbs and preconditions, as the adapter a session holds would offer
    def implementations(self) -> dict[str, Any]:
        return dict(MICRODUCK_VERBS)

    def preconditions(self) -> dict[str, Any]:
        return microduck_conditions()

    async def stop(self) -> None:
        await super().stop()
        if self.loop is not None and self.intents_of("move"):
            # what the session does to a goal it moves on from: the reason, then the abort
            self.loop.stop_reason = NEWER
            self.loop.executor.abort.set()


def _goal(duck: DuckFile, body: _Held, runs: Path) -> AgentLoop:
    return AgentLoop(
        RunConfig(
            duck=duck,
            provider=FakeProvider.for_duck(duck.name),
            transport=AttachedBody(body, microduck_manifest("mock")),
            runs_dir=runs,
        )
    )


async def test_a_goal_leaves_the_sessions_body_connected_held_and_where_it_put_it(
    hello_duck: DuckFile, tmp_path: Path
) -> None:
    """A goal is a whole run, and a run connects its body, heartbeats it, folds it and lets go of
    it. On the session's body it does none of those, which the session does once as it closes,
    and its record does not tell the session's connect and close again: its own verbs are all
    that reach the body."""
    body = _Held()
    result = await _goal(hello_duck, body, tmp_path).run()
    assert result.outcome == "success", result.reason
    assert body.done == [], f"the goal did what only the session may: {body.done}"
    assert body.connected and body.heartbeats == 0
    assert body.intents_of("move"), "the goal's own walk never reached the body"
    events = Transcript.read(result.run_dir / "transcript.jsonl")
    notes = [str(r.get("text") or "") for r in events if r["kind"] == "note"]
    assert not [n for n in notes if "lost a packet" in n or "torque released" in n], notes


async def test_a_goal_the_session_moves_on_from_ends_in_the_sessions_words(
    hello_duck: DuckFile, tmp_path: Path
) -> None:
    """The session ends a goal the way a flock stops a member, the reason first and then the
    abort, and the goal ends at its next check in those words, not as a kill switch nobody
    pressed."""
    body = _Held()
    loop = _goal(hello_duck, body, tmp_path)
    body.loop = loop
    result = await loop.run()
    assert result.outcome == "aborted"
    assert result.reason == NEWER


def test_a_failed_heartbeat_is_named_before_the_sessions_reason(
    hello_duck: DuckFile, tmp_path: Path
) -> None:
    """A body that stopped answering is the fact a reader most needs, whatever else asked the
    run to stop."""
    loop = _goal(hello_duck, _Held(), tmp_path)
    loop.stop_reason = NEWER
    assert loop._abort_reason() == NEWER
    loop.heartbeat.failure = RuntimeError("the robot stopped answering")
    assert loop._abort_reason() == "the robot stopped answering"
