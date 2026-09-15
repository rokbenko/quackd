"""The deliberation loop, end to end on the mock transport with the scripted provider."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from quackd.adapters.microduck import MicroduckAdapter
from quackd.agent.loop import AgentLoop, RunConfig, run_duck
from quackd.agent.providers.base import Exchange, ProviderError, ProviderTurn, ToolCall, Usage
from quackd.agent.providers.fake import FakeProvider
from quackd.agent.transcript import Transcript
from quackd.duckfile.schema import Budgets, DuckFile
from quackd.transport.mock import MockTransport

# the verdict comes first on every run now: the scripted pilot answers it as a rule, and the
# duck's own three verbs follow exactly as they did
GOLDEN_HELLO = ["assess_task", "quack", "walk", "quack", "declare_success"]


async def test_run_start_records_the_extra_body(hello_duck: DuckFile, tmp_path: Path) -> None:
    """A run whose model was told not to think reads nothing like one that was, and the
    transcript is the only place a reader can tell which of the two they are holding. The
    emit is a `getattr`, which would record None for ever if the attribute were renamed."""
    body = {"chat_template_kwargs": {"enable_thinking": False}}
    provider = FakeProvider.for_duck(hello_duck.name)
    provider.extra_body = body  # type: ignore[attr-defined]
    result = await run_duck(
        RunConfig(duck=hello_duck, provider=provider, transport=MockTransport(), runs_dir=tmp_path)
    )
    start = Transcript.read(result.run_dir / "transcript.jsonl")[0]
    assert start["kind"] == "run_start" and start["extra_body"] == body

    # a provider with no such attribute records None rather than raising
    plain = await run_duck(
        RunConfig(
            duck=hello_duck,
            provider=FakeProvider.for_duck(hello_duck.name),
            transport=MockTransport(),
            runs_dir=tmp_path / "plain",
        )
    )
    assert Transcript.read(plain.run_dir / "transcript.jsonl")[0]["extra_body"] is None


async def test_hello_world_golden(hello_duck: DuckFile, tmp_path: Path) -> None:
    transport = MockTransport()
    result = await run_duck(
        RunConfig(
            duck=hello_duck,
            provider=FakeProvider.for_duck("hello-world"),
            transport=transport,
            runs_dir=tmp_path,
        )
    )
    assert result.outcome == "success", result.reason
    # hello-world allows 5 llm calls and the verdict is the fifth: it fits, exactly
    assert result.steps == 3 and result.llm_calls == 5
    events = Transcript.read(result.run_dir / "transcript.jsonl")
    kinds = [e["kind"] for e in events]
    assert kinds[0] == "run_start" and kinds[-1] == "run_end"
    assert {"observation", "llm", "verb", "declare"} <= set(kinds)
    calls = [tc["name"] for e in events if e["kind"] == "llm" for tc in e["tool_calls"]]
    assert calls == GOLDEN_HELLO
    assert (result.run_dir / "summary.json").exists()
    assert [i.kind for i in transport.intents if i.kind != "stop"] == ["sound"] + ["move"] * 10 + [
        "sound"
    ]
    assert transport.intents[-1].kind == "stop"  # the loop always stops the duck on exit
    assert not transport.connected  # and closes the transport
    assert (result.run_dir / "frames").is_dir()


class NoToolProvider:
    name = "no-tool"
    model = "x"
    supports_vision = False

    async def step(
        self, system: str, history: list[Exchange], tools: list[dict[str, Any]]
    ) -> ProviderTurn:
        return ProviderTurn(tool_calls=[], text="I would rather talk.")


class ClockAdvancingProvider:
    name = "clock-advancing"
    model = "test"
    supports_vision = False

    def __init__(
        self, transport: MockTransport, seconds: float, declaration: str = "declare_success"
    ) -> None:
        self.transport = transport
        self.seconds = seconds
        self.declaration = declaration

    async def step(
        self, system: str, history: list[Exchange], tools: list[dict[str, Any]]
    ) -> ProviderTurn:
        await self.transport.sleep(self.seconds)
        return ProviderTurn(
            tool_calls=[ToolCall(name=self.declaration, arguments={"reason": "provider response"})]
        )


async def test_no_tool_call_is_reprompted_once_then_failure(
    hello_duck: DuckFile, tmp_path: Path
) -> None:
    result = await run_duck(
        RunConfig(
            duck=hello_duck, provider=NoToolProvider(), transport=MockTransport(), runs_dir=tmp_path
        )
    )
    assert result.outcome == "failure" and "no tool call" in result.reason
    assert result.llm_calls == 2


@pytest.mark.parametrize("declaration", ["declare_success", "declare_failure"])
async def test_time_budget_wins_over_late_declaration(
    hello_duck: DuckFile, tmp_path: Path, declaration: str
) -> None:
    hello_duck.frontmatter.budgets = Budgets(max_minutes=0.1)
    transport = MockTransport()
    result = await run_duck(
        RunConfig(
            duck=hello_duck,
            provider=ClockAdvancingProvider(transport, 7, declaration),
            transport=transport,
            runs_dir=tmp_path,
        )
    )

    assert result.outcome == "budget"
    assert result.reason == "max_minutes (0.1) exceeded"
    assert transport.now() == 7
    events = Transcript.read(result.run_dir / "transcript.jsonl")
    kinds = [event["kind"] for event in events]
    assert "llm" in kinds and "declare" not in kinds


async def test_provider_response_before_time_budget_is_processed(
    hello_duck: DuckFile, tmp_path: Path
) -> None:
    hello_duck.frontmatter.budgets = Budgets(max_minutes=0.1, max_llm_calls=1)
    transport = MockTransport()
    result = await run_duck(
        RunConfig(
            duck=hello_duck,
            provider=ClockAdvancingProvider(transport, 5),
            transport=transport,
            runs_dir=tmp_path,
        )
    )

    assert result.outcome == "success"
    assert result.reason == "provider response"
    assert result.llm_calls == 1
    assert transport.now() == 5
    events = Transcript.read(result.run_dir / "transcript.jsonl")
    assert "declare" in [event["kind"] for event in events]


async def test_budget_ends_the_run(hello_duck: DuckFile, tmp_path: Path) -> None:
    forever = FakeProvider(script=[ToolCall(name="quack", arguments={})])
    result = await run_duck(
        RunConfig(
            duck=hello_duck,
            provider=forever,
            transport=MockTransport(),
            runs_dir=tmp_path,
            max_steps=2,
        )
    )
    assert result.outcome == "budget" and "max_steps" in result.reason
    assert result.steps == 2


async def test_disallowed_verb_is_feedback(hello_duck: DuckFile, tmp_path: Path) -> None:
    naughty = FakeProvider(
        script=[
            ToolCall(name="kick", arguments={}),
            ToolCall(name="declare_failure", arguments={"reason": "refused"}),
        ]
    )
    transport = MockTransport()
    result = await run_duck(
        RunConfig(duck=hello_duck, provider=naughty, transport=transport, runs_dir=tmp_path)
    )
    assert result.outcome == "failure"
    events = Transcript.read(result.run_dir / "transcript.jsonl")
    verb_events = [e for e in events if e["kind"] == "verb"]
    assert verb_events[0]["name"] == "kick" and not verb_events[0]["ok"]
    assert "allowlist" in verb_events[0]["summary"]
    assert transport.intents_of("do") == []


async def test_heartbeat_failure_aborts_the_run(hello_duck: DuckFile, tmp_path: Path) -> None:
    transport = MockTransport(fail_heartbeat_after=0)
    forever = FakeProvider(script=[ToolCall(name="walk", arguments={"duration_s": 2.0})])
    result = await run_duck(
        RunConfig(
            duck=hello_duck,
            provider=forever,
            transport=transport,
            runs_dir=tmp_path,
            heartbeat_period_s=0.001,
        )
    )
    assert result.outcome == "aborted"
    assert "heartbeat" in result.reason
    assert transport.stops >= 1


async def test_dry_run_touches_nothing(hello_duck: DuckFile, tmp_path: Path) -> None:
    transport = MockTransport()
    result = await run_duck(
        RunConfig(
            duck=hello_duck,
            provider=FakeProvider.for_duck("hello-world"),
            transport=transport,
            runs_dir=tmp_path,
            dry_run=True,
        )
    )
    assert result.outcome == "success"
    assert [i.kind for i in transport.intents] == ["stop"]  # only the final safety stop


# ── the trace: the run narrates itself ──────────────────────────────────────────────────


class ThinkingProvider:
    """A model that reasons out loud, which no scripted strategy does."""

    name = "thinker"
    model = "test"
    supports_vision = False

    def __init__(self, *calls: ToolCall) -> None:
        self.script = list(calls)
        self.calls = 0

    async def step(
        self, system: str, history: list[Exchange], tools: list[dict[str, Any]]
    ) -> ProviderTurn:
        call = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        return ProviderTurn(
            tool_calls=[call],
            text="on it",
            thinking=f"turn {self.calls}: I will {call.name}",
            usage=Usage(input_tokens=100, output_tokens=10),
            stop_reason="tool_use",
        )


async def test_the_transcript_carries_the_whole_conversation(
    hello_duck: DuckFile, tmp_path: Path
) -> None:
    """Every step the run takes on the model's behalf is a line: what was asked, what it
    thought, what it answered, what the executor decided, what went to the robot."""
    result = await run_duck(
        RunConfig(
            duck=hello_duck,
            provider=ThinkingProvider(
                ToolCall(name="quack", arguments={"text": "hi"}),
                ToolCall(name="declare_success", arguments={"reason": "quacked"}),
            ),
            transport=MockTransport(),
            runs_dir=tmp_path,
        )
    )
    assert result.outcome == "success"
    events = Transcript.read(result.run_dir / "transcript.jsonl")
    kinds = {e["kind"] for e in events}
    assert {"llm_request", "verb_start", "intent", "verb_end"} <= kinds

    llm = next(e for e in events if e["kind"] == "llm")
    assert llm["thinking"] == "turn 1: I will quack"
    assert llm["latency_s"] >= 0 and llm["usage_total"]["input_tokens"] == 100

    request = next(e for e in events if e["kind"] == "llm_request")
    assert request["messages"] == 1 and request["reprompt"] is False

    start = next(e for e in events if e["kind"] == "verb_start")
    assert start["name"] == "quack" and start["source"] == "agent" and start["nested"] is False

    intent = next(e for e in events if e["kind"] == "intent")
    assert intent["intent"] == "sound" and intent["accepted"] is True

    end = next(e for e in events if e["kind"] == "verb_end")
    assert end["outcome"] == "ok" and end["intents"] == {"sound": 1} and end["elapsed_s"] >= 0
    # the loop's own `verb` record is unchanged, so everything that reads it still can
    verb = next(e for e in events if e["kind"] == "verb")
    assert verb["name"] == "quack" and verb["ok"] is True


async def test_the_final_safety_stop_is_in_the_trace_too(
    hello_duck: DuckFile, tmp_path: Path
) -> None:
    """The intents that matter most are the ones sent because something went wrong."""
    result = await run_duck(
        RunConfig(
            duck=hello_duck,
            provider=FakeProvider.for_duck("hello-world"),
            transport=MockTransport(),
            runs_dir=tmp_path,
        )
    )
    events = Transcript.read(result.run_dir / "transcript.jsonl")
    stops = [e for e in events if e["kind"] == "intent" and e["intent"] == "stop"]
    assert stops, "the run always stops the robot on the way out, and must say so"


async def test_a_provider_that_fails_says_so_instead_of_exiting_unexpectedly(
    hello_duck: DuckFile, tmp_path: Path
) -> None:
    """A bad key, a 429 or a dropped connection is what a first real run hits. The run used
    to end with `loop exited unexpectedly` and no record of the call that failed."""

    class Failing:
        name, model, supports_vision = "failing", "test", False

        async def step(self, system: str, history: Any, tools: Any) -> ProviderTurn:
            raise ProviderError("anthropic: rate limited (retry-after 7s)")

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    with pytest.raises(ProviderError):
        await run_duck(
            RunConfig(
                duck=hello_duck,
                provider=Failing(),
                transport=MockTransport(),
                run_dir=run_dir,
                runs_dir=tmp_path,
            )
        )
    events = Transcript.read(run_dir / "transcript.jsonl")
    failed = next(e for e in events if e["kind"] == "llm")
    assert "rate limited" in failed["error"] and failed["latency_s"] >= 0
    end = next(e for e in events if e["kind"] == "run_end")
    assert end["outcome"] == "error" and "rate limited" in end["reason"]


async def test_a_cancelled_run_ends_as_an_abort_that_still_stops_and_records(
    hello_duck: DuckFile, tmp_path: Path
) -> None:
    """`KeyboardInterrupt` and `CancelledError` are not `Exception`, so neither reached the
    error branch and `run_end` kept its default, `loop exited unexpectedly` — the very string
    the trace work claimed to have removed. The CLI's second Ctrl-C is this path."""

    class Stalling:
        name, model, supports_vision = "stalling", "test", False

        async def step(self, system: str, history: Any, tools: Any) -> ProviderTurn:
            await asyncio.sleep(10)
            raise AssertionError("never reached")

    run_dir = tmp_path / "cancelled"
    run_dir.mkdir()
    transport = MockTransport()
    task = asyncio.create_task(
        run_duck(
            RunConfig(
                duck=hello_duck,
                provider=Stalling(),
                transport=transport,
                run_dir=run_dir,
                runs_dir=tmp_path,
            )
        )
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    events = Transcript.read(run_dir / "transcript.jsonl")
    end = next(e for e in events if e["kind"] == "run_end")
    assert end["outcome"] == "aborted" and "CancelledError" in end["reason"]
    assert any(e["kind"] == "note" and "interrupted" in e["text"] for e in events)
    assert transport.intents[-1].kind == "stop" and not transport.connected
    assert (run_dir / "summary.json").exists()


async def test_a_record_that_fails_at_run_end_still_gets_its_summary_and_is_closed(
    hello_duck: DuckFile, tmp_path: Path
) -> None:
    """A disk that fills at the last line used to skip summary.json, leak the handle and
    replace the run's own outcome with an OSError."""
    from quackd.agent.loop import AgentLoop

    run_dir = tmp_path / "full"
    run_dir.mkdir()
    loop = AgentLoop(
        RunConfig(
            duck=hello_duck,
            provider=FakeProvider.for_duck("hello-world"),
            transport=MockTransport(),
            run_dir=run_dir,
            runs_dir=tmp_path,
        )
    )
    good = loop.transcript.sink

    def record(event: Any) -> None:
        if event.kind == "run_end":
            raise OSError("disk full")
        good(event)

    loop.tracer.record = record
    with pytest.raises(OSError, match="disk full"):
        await loop.run()
    assert (run_dir / "summary.json").exists()
    assert loop.transcript._fh.closed


def test_writing_to_a_closed_transcript_is_a_no_op(tmp_path: Path) -> None:
    """A verb task cancelled during teardown narrates its last intent after the record has
    closed; that must not raise inside a task nobody awaits."""
    from quackd.trace import TraceEvent

    t = Transcript(tmp_path)
    t.close()
    t.write("intent", intent="stop")
    t.sink(TraceEvent("intent", 0.0, {"intent": "stop"}))
    assert t.events == 0


async def test_a_console_sees_the_run_as_it_happens(hello_duck: DuckFile, tmp_path: Path) -> None:
    seen: list[str] = []
    await run_duck(
        RunConfig(
            duck=hello_duck,
            provider=FakeProvider.for_duck("hello-world"),
            transport=MockTransport(),
            runs_dir=tmp_path,
            trace=lambda event: seen.append(event.kind),
        )
    )
    assert seen[0] == "run_start" and seen[-1] == "run_end"
    assert {"observation", "llm", "verb_start", "intent", "verb_end", "declare"} <= set(seen)


async def test_a_broken_console_never_ends_a_run(hello_duck: DuckFile, tmp_path: Path) -> None:
    """A terminal that cannot print is not a reason to stop a robot mid-task."""

    def broken(_event: Any) -> None:
        raise RuntimeError("the terminal went away")

    result = await run_duck(
        RunConfig(
            duck=hello_duck,
            provider=FakeProvider.for_duck("hello-world"),
            transport=MockTransport(),
            runs_dir=tmp_path,
            trace=broken,
        )
    )
    assert result.outcome == "success"
    assert Transcript.read(result.run_dir / "transcript.jsonl")  # the record is unaffected


async def test_the_summary_counts_the_events_a_broken_console_dropped(
    hello_duck: DuckFile, tmp_path: Path
) -> None:
    """A console that raises on every event produced a silent trace, an unchanged exit code
    and no line anywhere saying events had been dropped."""

    def broken(_event: Any) -> None:
        raise RuntimeError("the terminal went away")

    result = await run_duck(
        RunConfig(
            duck=hello_duck,
            provider=FakeProvider.for_duck("hello-world"),
            transport=MockTransport(),
            runs_dir=tmp_path,
            trace=broken,
        )
    )
    assert result.trace_dropped > 0
    summary = json.loads((result.run_dir / "summary.json").read_text(encoding="utf-8"))
    end = next(
        e for e in Transcript.read(result.run_dir / "transcript.jsonl") if e["kind"] == "run_end"
    )
    # the record's own count is one short of the run's, and can only ever be: it is taken
    # while the summary is built, and emitting `run_end` with it is one more event to drop.
    # The CLI prints the result's, which is complete.
    assert summary["trace_dropped"] == end["trace_dropped"] == result.trace_dropped - 1


async def test_thinking_on_the_reprompt_turn_is_recorded(
    hello_duck: DuckFile, tmp_path: Path
) -> None:
    """The re-prompt is a second call to the model in the same step, and nothing asserted
    that the request was marked as one or that its answer's reasoning was kept."""

    class Dithering:
        name, model, supports_vision = "dithering", "test", False

        def __init__(self) -> None:
            self.calls = 0

        async def step(self, system: str, history: Any, tools: Any) -> ProviderTurn:
            self.calls += 1
            if self.calls == 1:
                return ProviderTurn(tool_calls=[], text="hmm", thinking="turn 1: still deciding")
            return ProviderTurn(
                tool_calls=[ToolCall(name="declare_success", arguments={"reason": "done"})],
                thinking="turn 2: it wants exactly one tool",
            )

    result = await run_duck(
        RunConfig(
            duck=hello_duck,
            provider=Dithering(),
            transport=MockTransport(),
            runs_dir=tmp_path,
        )
    )
    assert result.outcome == "success"
    events = Transcript.read(result.run_dir / "transcript.jsonl")
    requests = [e for e in events if e["kind"] == "llm_request"]
    assert [r["reprompt"] for r in requests] == [False, True]
    assert [e["thinking"] for e in events if e["kind"] == "llm"][1] == (
        "turn 2: it wants exactly one tool"
    )
    enforce = next(e for e in events if e["kind"] == "enforce")
    assert enforce["text"] == "You must call exactly one tool. Choose now."


async def test_a_composite_is_traced_as_nested_pairs_with_the_parents_tally(
    kick_duck: DuckFile, tmp_path: Path
) -> None:
    """A composite sends nothing itself: every intent `approach_and` reports came from the
    `go_to` and the `kick` it ran. The tally is a chain of `ContextVar` frames, so a
    regression there is silent — the parent would keep reporting a number, just a smaller
    one, and a reader would believe it."""
    from quackd.perception.color_blob import ColorBlobDetector
    from quackd.transport.sim2d import Sim2DTransport

    kick_duck.frontmatter.verbs.allow = [*kick_duck.frontmatter.verbs.allow, "approach_and"]
    seen: list[Any] = []
    result = await run_duck(
        RunConfig(
            duck=kick_duck,
            provider=FakeProvider(
                script=[
                    ToolCall(name="search_scan", arguments={"target": "ball"}),
                    ToolCall(
                        name="approach_and",
                        arguments={"target": "ball", "stop_distance": 0.22, "then": "kick"},
                    ),
                    ToolCall(name="declare_success", arguments={"reason": "kicked"}),
                ]
            ),
            transport=Sim2DTransport(seed=6),
            detector=ColorBlobDetector(),
            runs_dir=tmp_path,
            trace=seen.append,
        )
    )
    assert result.outcome == "success", result.reason

    opened = next(
        i for i, e in enumerate(seen) if e.kind == "verb_start" and e.data["name"] == "approach_and"
    )
    closed = next(
        i for i, e in enumerate(seen) if e.kind == "verb_end" and e.data["name"] == "approach_and"
    )
    assert seen[opened].data["nested"] is False and seen[closed].data["nested"] is False

    inside = seen[opened + 1 : closed]
    assert all(e.data["nested"] is True for e in inside if e.kind in ("verb_start", "verb_end"))
    children = [e for e in inside if e.kind == "verb_end"]
    assert [e.data["name"] for e in children] == ["go_to", "kick"]

    sent = sum(sum(e.data["intents"].values()) for e in children)
    assert sent > 0, "the children really did drive the robot"
    assert sum(seen[closed].data["intents"].values()) >= sent


async def test_the_log_callback_still_gets_the_lines_that_only_it_had(
    hello_duck: DuckFile, tmp_path: Path
) -> None:
    """`log` is a contract other callers rely on: the flock's member records, the MCP
    logger, and tests that assert on what a run said. The trace observes it, never replaces
    it."""
    # a v1 task may allow more than it needs; a verb this body lacks is dropped with a line
    hello_duck.frontmatter.duck = 1
    hello_duck.frontmatter.requires = ["quack"]
    hello_duck.frontmatter.verbs.allow = ["quack", "walk", "stop", "fly"]
    lines: list[str] = []
    seen: list[Any] = []
    await run_duck(
        RunConfig(
            duck=hello_duck,
            provider=FakeProvider.for_duck("hello-world"),
            transport=MockTransport(),
            runs_dir=tmp_path,
            log=lines.append,
            trace=seen.append,
        )
    )
    assert any("does not have fly" in line for line in lines)
    notes = [e.data["text"] for e in seen if e.kind == "note"]
    assert any("does not have fly" in note for note in notes)


def test_intents_are_buffered_until_the_next_event_flushes_them(tmp_path: Path) -> None:
    """The flush was a syscall on the event loop between two deadman resends of a steering
    verb. Intents ride the buffer; anything else, `verb_end` included, puts them on disk."""
    transcript = Transcript(tmp_path)
    path = transcript.path
    try:
        for _ in range(20):
            transcript.write("intent", intent="move", accepted=True)
        assert path.read_text(encoding="utf-8") == "", "an intent must not reach the disk alone"
        transcript.write("verb_end", name="walk", outcome="ok")
        assert len(Transcript.read(path)) == 21, "the verb ending must flush every intent"
    finally:
        transcript.close()


class CapturingProvider:
    """Records the system prompt and every observation, then declares success."""

    name = "capturing"
    model = "test"
    supports_vision = False

    def __init__(self) -> None:
        self.systems: list[str] = []
        self.observations: list[str] = []

    async def step(
        self, system: str, history: list[Exchange], tools: list[dict[str, Any]]
    ) -> ProviderTurn:
        self.systems.append(system)
        self.observations.append(history[-1].observation.text)
        return ProviderTurn(
            tool_calls=[ToolCall(name="declare_success", arguments={"reason": "done"})]
        )


async def test_the_stand_ins_a_robot_declares_are_told_to_the_model(
    hello_duck: DuckFile, tmp_path: Path
) -> None:
    """`extras.assumptions` is what a backend says quackd is standing in for. It reached the
    transcript and `FakeProvider`, and no further: every real provider sends `obs.text`, which
    is built from `state.summary()`, and neither had a branch for it. So the docs said a
    transcript never implies more than happened while the model was told nothing at all.
    """
    from quackd.transport.base import DuckState

    stand_ins = [
        "kick is a scripted impulse, not the robot's own kick policy",
        "a fall is recovered by standing the model up; upstream ships no get-up policy",
    ]
    transport = MockTransport(
        states=[DuckState(policy="mock", posture="standing", extras={"assumptions": stand_ins})]
    )
    provider = CapturingProvider()
    await run_duck(
        RunConfig(duck=hello_duck, provider=provider, transport=transport, runs_dir=tmp_path)
    )
    system = provider.systems[0]
    assert "## What is a stand-in on this robot" in system
    for sentence in stand_ins:
        assert sentence in system, "verbatim, in the robot's own words"
    # and the observation points at them, for a pilot with no system prompt at all (MCP)
    assert "stand-ins=2-listed-in-extras.assumptions" in provider.observations[0]


async def test_a_robot_that_claims_no_stand_ins_gets_no_such_section(
    hello_duck: DuckFile, tmp_path: Path
) -> None:
    provider = CapturingProvider()
    await run_duck(
        RunConfig(duck=hello_duck, provider=provider, transport=MockTransport(), runs_dir=tmp_path)
    )
    assert "stand-in" not in provider.systems[0]
    assert "stand-ins=" not in provider.observations[0]


ARM_DUCK = """\
---
duck: {version}
name: lift-the-mug
description: Pick up the mug.
verbs:
  allow: [observe, report_state, stop]
success: [The mug is up.]
{block}---
# Task
Pick it up.
"""
_OVERRIDE = """datasheet:
  payload_kg: {value: 0.3, confidence: measured, source: weighed with the printed gripper}
"""


async def test_a_task_files_datasheet_reaches_the_prompt_the_executor_and_the_record(
    tmp_path: Path,
) -> None:
    from quackd.adapters.factory import make_adapter
    from quackd.duckfile.parser import parse_duck_text

    provider = CapturingProvider()
    adapter = make_adapter("lerobot:mock")
    loop = AgentLoop(
        RunConfig(
            duck=parse_duck_text(ARM_DUCK.format(version=2, block=_OVERRIDE)),
            provider=provider,
            transport=adapter,
            runs_dir=tmp_path,
        )
    )
    result = await loop.run()
    assert result.outcome == "success", result.reason

    assert (
        "0.3 kg (measured: the task file, weighed with the printed gripper)" in provider.systems[0]
    )
    assert "0.5 kg (estimate" not in provider.systems[0], "the vendor figure was corrected"
    sheet = loop.executor.manifest.datasheet if loop.executor.manifest else None
    assert sheet is not None and sheet.payload_kg is not None and sheet.payload_kg.value == 0.3
    start = Transcript.read(result.run_dir / "transcript.jsonl")[0]
    assert start["robot"]["datasheet"]["payload_kg"]["source"].startswith("the task file")


async def test_without_a_correction_the_body_speaks_for_itself(tmp_path: Path) -> None:
    from quackd.adapters.factory import make_adapter
    from quackd.duckfile.parser import parse_duck_text

    provider = CapturingProvider()
    result = await run_duck(
        RunConfig(
            duck=parse_duck_text(ARM_DUCK.format(version=1, block="")),
            provider=provider,
            transport=make_adapter("lerobot:mock"),
            runs_dir=tmp_path,
        )
    )
    assert result.outcome == "success", result.reason
    assert "0.5 kg (estimate: one vendor's listing)" in provider.systems[0]
    assert "task file" not in provider.systems[0]


def _verdict_call(word: str, reason: str, **extra: Any) -> ToolCall:
    return ToolCall(name="assess_task", arguments={"verdict": word, "reason": reason, **extra})


async def test_the_rule_answers_the_gate_before_its_own_first_verb(
    hello_duck: DuckFile, tmp_path: Path
) -> None:
    """The scripted pilot has no judgement of a body, so it says so and goes on. Without
    that, every keyless run in the README would stop at the gate."""
    transport = MockTransport()
    result = await run_duck(
        RunConfig(
            duck=hello_duck,
            provider=FakeProvider(
                script=[
                    ToolCall(name="walk", arguments={"vx": 0.1, "duration_s": 1.0}),
                    ToolCall(name="declare_success", arguments={"reason": "walked"}),
                ]
            ),
            transport=transport,
            runs_dir=tmp_path,
        )
    )
    assert result.outcome == "success", result.reason
    events = Transcript.read(result.run_dir / "transcript.jsonl")
    calls = [tc["name"] for e in events if e["kind"] == "llm" for tc in e["tool_calls"]]
    assert calls == ["assess_task", "walk", "declare_success"]
    assert [i.kind for i in transport.intents if i.kind == "move"]
    assessed = next(e for e in events if e["kind"] == "assess")
    assert assessed["verdict"] == "feasible"
    assert "a rule has no judgement of the body" in assessed["reason"]


async def test_a_verb_before_any_verdict_is_refused_and_the_pilot_is_told_why(
    hello_duck: DuckFile, tmp_path: Path
) -> None:
    class Impatient:
        """A pilot that reaches for a leg before it has judged the task."""

        name = "impatient"
        model = "test"
        supports_vision = False

        def __init__(self) -> None:
            self.calls = 0

        async def step(
            self, system: str, history: list[Exchange], tools: list[dict[str, Any]]
        ) -> ProviderTurn:
            self.calls += 1
            call = (
                ToolCall(name="walk", arguments={"vx": 0.1, "duration_s": 1.0})
                if self.calls == 1
                else ToolCall(name="declare_failure", arguments={"reason": "refused"})
            )
            return ProviderTurn(tool_calls=[call])

    transport = MockTransport()
    result = await run_duck(
        RunConfig(duck=hello_duck, provider=Impatient(), transport=transport, runs_dir=tmp_path)
    )
    assert result.outcome == "failure"
    events = Transcript.read(result.run_dir / "transcript.jsonl")
    verb = next(e for e in events if e["kind"] == "verb")
    assert verb["name"] == "walk" and verb["ok"] is False
    assert "moves the body" in verb["summary"] and "assess_task" in verb["summary"]
    gate = next(e for e in events if e["kind"] == "gate" and e.get("gate") == "verdict")
    assert gate["outcome"] == "refused"
    assert [i.kind for i in transport.intents if i.kind == "move"] == []


async def test_an_infeasible_verdict_ends_the_run_before_anything_moves(
    hello_duck: DuckFile, tmp_path: Path
) -> None:
    transport = MockTransport()
    result = await run_duck(
        RunConfig(
            duck=hello_duck,
            provider=FakeProvider(
                script=[
                    _verdict_call(
                        "infeasible",
                        "the basket looks like 3 kg of clothes and this body has no arms",
                        limits_consulted=["manipulator", "payload_kg"],
                        estimates=[
                            {
                                "object": "laundry basket",
                                "quantity": "mass_kg",
                                "value": 3.0,
                                "basis": "image",
                                "confidence": "medium",
                            }
                        ],
                        needs={"payload_kg": 3.0, "manipulator": "gripper"},
                    ),
                    ToolCall(name="walk", arguments={"vx": 0.1, "duration_s": 1.0}),
                ]
            ),
            transport=transport,
            runs_dir=tmp_path,
        )
    )
    assert result.outcome == "infeasible"
    assert result.steps == 0 and result.llm_calls == 1
    assert not result.ok
    assert "3 kg of clothes" in result.reason
    assert "No shipped body meets needs" in result.reason, "the hint names what could"
    assert [i.kind for i in transport.intents if i.kind != "stop"] == []

    events = Transcript.read(result.run_dir / "transcript.jsonl")
    assessed = next(e for e in events if e["kind"] == "assess")
    assert assessed["verdict"] == "infeasible" and assessed["ends_run"] is True
    assert assessed["estimates"][0]["object"] == "laundry basket"
    assert assessed["needs"] == {"payload_kg": 3.0, "manipulator": "gripper"}
    assert assessed["limits_consulted"] == ["manipulator", "payload_kg"]
    summary = json.loads((result.run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["outcome"] == "infeasible"


async def test_an_uncertain_verdict_asks_the_person_in_the_room(
    hello_duck: DuckFile, tmp_path: Path
) -> None:
    asked: list[str] = []

    def no(why: str) -> bool:
        asked.append(why)
        return False

    transport = MockTransport()
    result = await run_duck(
        RunConfig(
            duck=hello_duck,
            provider=FakeProvider(
                script=[
                    _verdict_call(
                        "uncertain",
                        "the basket is out of frame, so its weight is a guess",
                        needs={"payload_kg": 2.0},
                    ),
                    ToolCall(name="walk", arguments={"vx": 0.1, "duration_s": 1.0}),
                ]
            ),
            transport=transport,
            runs_dir=tmp_path,
            decide=no,
        )
    )
    assert result.outcome == "aborted", "a person stopping the run is the kill switch's kind"
    assert "the human said no" in result.reason
    assert "out of frame" in result.reason
    assert [i.kind for i in transport.intents if i.kind != "stop"] == []
    assert len(asked) == 1
    assert "payload_kg=2" in asked[0] and "not sure" in asked[0]
    events = Transcript.read(result.run_dir / "transcript.jsonl")
    assert next(e for e in events if e["kind"] == "assess")["human"] == "no_go"


async def test_a_person_who_says_go_clears_the_gate(hello_duck: DuckFile, tmp_path: Path) -> None:
    transport = MockTransport()
    result = await run_duck(
        RunConfig(
            duck=hello_duck,
            provider=FakeProvider(
                script=[
                    _verdict_call("uncertain", "cannot see the thing from here"),
                    ToolCall(name="walk", arguments={"vx": 0.1, "duration_s": 1.0}),
                    ToolCall(name="declare_success", arguments={"reason": "walked"}),
                ]
            ),
            transport=transport,
            runs_dir=tmp_path,
            decide=lambda _why: True,
        )
    )
    assert result.outcome == "success", result.reason
    assert [i.kind for i in transport.intents if i.kind == "move"]
    events = Transcript.read(result.run_dir / "transcript.jsonl")
    assert next(e for e in events if e["kind"] == "assess")["human"] == "go"


async def test_with_nobody_to_ask_the_pilot_is_told_to_decide_itself(
    hello_duck: DuckFile, tmp_path: Path
) -> None:
    transport = MockTransport()
    result = await run_duck(
        RunConfig(
            duck=hello_duck,
            provider=FakeProvider(
                script=[
                    _verdict_call("uncertain", "cannot see the thing from here"),
                    ToolCall(name="walk", arguments={"vx": 0.1, "duration_s": 1.0}),
                    _verdict_call("feasible", "looked again: it is a tennis ball"),
                    ToolCall(name="walk", arguments={"vx": 0.1, "duration_s": 1.0}),
                    ToolCall(name="declare_success", arguments={"reason": "walked"}),
                ]
            ),
            transport=transport,
            runs_dir=tmp_path,
            decide=None,
        )
    )
    assert result.outcome == "success", result.reason
    events = Transcript.read(result.run_dir / "transcript.jsonl")
    observations = [e["text"] for e in events if e["kind"] == "observation"]
    assert any("decide yourself" in text for text in observations)
    refused = [e for e in events if e["kind"] == "verb" and not e["ok"]]
    assert refused and "assess_task" in refused[0]["summary"]
    assert [e["human"] for e in events if e["kind"] == "assess"] == [None, None]


async def test_a_later_verdict_can_still_end_the_run(hello_duck: DuckFile, tmp_path: Path) -> None:
    transport = MockTransport()
    result = await run_duck(
        RunConfig(
            duck=hello_duck,
            provider=FakeProvider(
                script=[
                    _verdict_call("feasible", "looks light from here"),
                    ToolCall(name="quack", arguments={}),
                    _verdict_call(
                        "infeasible",
                        "close up it is a full crate, not a box",
                        needs={"payload_kg": 5.0},
                    ),
                ]
            ),
            transport=transport,
            runs_dir=tmp_path,
        )
    )
    assert result.outcome == "infeasible"
    assert result.steps == 1, "the quack ran before the pilot changed its mind"
    assert "full crate" in result.reason


async def test_a_feasible_verdict_is_held_to_its_own_datasheet(
    hello_duck: DuckFile, tmp_path: Path
) -> None:
    """A pilot may not move its body on a need its own sheet does not meet.

    `missing_needs` already held another robot's bid to its datasheet at the coordinator,
    and nothing held a pilot's verdict about its own body to its own sheet, so a `needs`
    naming a figure nobody published passed straight through. The Microduck's endurance is
    not published, and a run that says the task needs 45 minutes of it is saying, in its own
    two fields, both that it depends on that number and that the body is fine. The pilot is
    told which need is unmet and can assess again, the same way a verdict carrying `human`
    is refused rather than quietly stripped.
    """
    result = await run_duck(
        RunConfig(
            duck=hello_duck,
            provider=FakeProvider(
                script=[
                    _verdict_call("feasible", "it can patrol", needs={"endurance_min": 45}),
                    _verdict_call("infeasible", "endurance is not published"),
                ]
            ),
            transport=MicroduckAdapter(MockTransport()),
            runs_dir=tmp_path,
        )
    )
    assert result.outcome == "infeasible", result.reason
    events = Transcript.read(result.run_dir / "transcript.jsonl")
    assessed = [e for e in events if e["kind"] == "assess"]
    assert assessed[0]["ok"] is False
    assert "endurance_min >= 45 (not published)" in assessed[0]["summary"]
    # the second one ends the run, which assess records the way infeasible always does
    assert assessed[1]["verdict"] == "infeasible"
    assert "endurance is not published" in assessed[1]["summary"]


async def test_a_need_this_body_meets_still_passes(hello_duck: DuckFile, tmp_path: Path) -> None:
    """The check refuses what the sheet does not cover, and nothing else.

    Without this, a check that refused every `needs` would pass the test above and break
    every run that fills the field in honestly. The Microduck is legged and rated for a flat
    indoor floor, so a verdict asking for exactly that is the sheet agreeing with itself.
    """
    result = await run_duck(
        RunConfig(
            duck=hello_duck,
            provider=FakeProvider(
                script=[
                    _verdict_call(
                        "feasible",
                        "it can walk there",
                        needs={"mobility": "legged", "terrain": "indoor_flat"},
                    ),
                    ToolCall(name="declare_success", arguments={"reason": "done"}),
                ]
            ),
            transport=MicroduckAdapter(MockTransport()),
            runs_dir=tmp_path,
        )
    )
    assert result.outcome == "success", result.reason
    events = Transcript.read(result.run_dir / "transcript.jsonl")
    assessed = [e for e in events if e["kind"] == "assess"]
    assert assessed[0]["ok"] is True


async def test_an_invalid_verdict_is_refused_and_the_run_goes_on(
    hello_duck: DuckFile, tmp_path: Path
) -> None:
    result = await run_duck(
        RunConfig(
            duck=hello_duck,
            provider=FakeProvider(
                script=[
                    ToolCall(name="assess_task", arguments={"verdict": "maybe", "reason": "hm"}),
                    ToolCall(
                        name="assess_task",
                        arguments={"verdict": "feasible", "reason": "fine", "human": "go"},
                    ),
                    _verdict_call("feasible", "fine"),
                    ToolCall(name="declare_success", arguments={"reason": "done"}),
                ]
            ),
            transport=MockTransport(),
            runs_dir=tmp_path,
        )
    )
    assert result.outcome == "success", result.reason
    events = Transcript.read(result.run_dir / "transcript.jsonl")
    assessed = [e for e in events if e["kind"] == "assess"]
    assert assessed[0]["ok"] is False and "invalid assess_task" in assessed[0]["summary"]
    assert assessed[1]["ok"] is False and "only a person sets it" in assessed[1]["summary"]
    assert assessed[2]["ok"] is True


async def test_the_prompt_offers_the_tool_and_states_the_rule(
    hello_duck: DuckFile, tmp_path: Path
) -> None:
    provider = CapturingProvider()
    result = await run_duck(
        RunConfig(duck=hello_duck, provider=provider, transport=MockTransport(), runs_dir=tmp_path)
    )
    events = Transcript.read(result.run_dir / "transcript.jsonl")
    assert "assess_task" in events[0]["tools"]
    system = provider.systems[0]
    assert "Before the first verb that moves the body, call " in system
    assert "assess_task" in system
    assert "the run ends, nothing moves" in system
