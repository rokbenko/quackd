"""The log: one event stream, and the views that must not lie about it or crash on it."""

from __future__ import annotations

import asyncio
import io
from typing import Any

import pytest
from rich.console import Console

from quackd.agent.providers.catalogue import PRICES_CHECKED, Price
from quackd.log import (
    ConsoleLog,
    EventLog,
    LineLog,
    LogEvent,
    LoggedTransport,
    _price_line,
    cap_lines,
    capture_sink,
    capturing,
    counting,
    fan_out,
    flock_caption,
    fmt_duration,
    fmt_value,
    log_enabled_default,
    parse_thinking_limit,
    prompt_shown_default,
    render_call,
    render_events,
    render_lines,
    thinking_limit_default,
)
from quackd.transport.base import Ack, Intent
from quackd.transport.mock import MockTransport


def events(event_log: EventLog) -> list[LogEvent]:
    seen: list[LogEvent] = []
    event_log.add(seen.append)
    return seen


def lines(sink_events: list[LogEvent], **kwargs: Any) -> list[str]:
    out: list[str] = []
    view = LineLog(lambda text, _style: out.append(text), **kwargs)
    for event in sink_events:
        view(event)
    view.flush()
    return out


# ── the event_log ──────────────────────────────────────────────────────────────────────────


def test_the_record_sink_gets_every_event_and_its_failure_is_the_runs() -> None:
    """The transcript is the record: a write that fails must not be swallowed the way a
    console that cannot print is."""

    def broken(_event: LogEvent) -> None:
        raise OSError("disk full")

    with pytest.raises(OSError, match="disk full"):
        EventLog(record=broken).emit("run_start")


def test_an_observer_that_raises_never_ends_a_run() -> None:
    written: list[str] = []

    def broken(_event: LogEvent) -> None:
        raise ValueError("a terminal that cannot print")

    event_log = EventLog(record=lambda e: written.append(e.kind), observers=[broken])
    event_log.emit("llm", text="hello")
    assert written == ["llm"] and event_log.dropped == 1


def test_a_payload_may_have_a_field_called_kind() -> None:
    """The intent's own kind used to collide with the event kind, and the transcript wrote
    `{"kind": "move"}` for what was an `intent` event."""
    seen: list[LogEvent] = []
    EventLog(record=seen.append).emit("intent", intent="move", kind="not the event kind")
    assert seen[0].kind == "intent" and seen[0].data["kind"] == "not the event kind"


def test_events_are_stamped_in_order() -> None:
    event_log = EventLog()
    seen = events(event_log)
    event_log.emit("a")
    event_log.emit("b")
    assert [e.kind for e in seen] == ["a", "b"] and seen[0].t <= seen[1].t


# ── the transport verbs see ─────────────────────────────────────────────────────────────


async def test_every_intent_and_stop_is_an_event() -> None:
    event_log = EventLog()
    seen = events(event_log)
    inner = MockTransport()
    logged = LoggedTransport(inner, event_log)
    await logged.send_intent(Intent.move(0.2, 0.0, 0.1))
    await logged.stop()
    assert [e.kind for e in seen] == ["intent", "intent"]
    assert seen[0].data == {
        "intent": "move",
        "params": {"vx": 0.2, "vy": 0.0, "wz": 0.1},
        "accepted": True,
        "reason": None,
        "robot_t": 0.0,  # the mock's clock only moves on `sleep`
    }
    assert seen[1].data["intent"] == "stop"
    # and the real transport really got them
    assert [i.kind for i in inner.intents] == ["move", "stop"]


async def test_a_refused_intent_says_so() -> None:
    event_log = EventLog()
    seen = events(event_log)
    logged = LoggedTransport(MockTransport(refuse_kinds={"sound"}), event_log)
    ack = await logged.send_intent(Intent.sound("greet", "hi"))
    assert not ack.accepted
    assert seen[0].data["accepted"] is False and "refuses" in seen[0].data["reason"]


async def test_a_transport_that_raises_is_logged_and_still_raises() -> None:
    class Broken(MockTransport):
        async def send_intent(self, intent: Intent) -> Ack:
            raise ConnectionError("the link is gone")

    event_log = EventLog()
    seen = events(event_log)
    with pytest.raises(ConnectionError):
        await LoggedTransport(Broken(), event_log).send_intent(Intent.stop())
    assert seen[0].data["accepted"] is False and "ConnectionError" in seen[0].data["reason"]


async def test_a_stop_that_raises_is_logged_and_still_raises() -> None:
    """A stop that failed is the single most important line in the log: it is the moment the
    brake did not answer. `LoggedTransport.stop` emits before it re-raises, so the caller
    still gets the exception it has to act on and the record still has the line."""

    class Broken(MockTransport):
        async def stop(self) -> None:
            raise ConnectionError("the socket is gone")

    event_log = EventLog()
    seen = events(event_log)
    with pytest.raises(ConnectionError, match="the socket is gone"):
        await LoggedTransport(Broken(), event_log).stop()
    assert [e.kind for e in seen] == ["intent"], "the emitted event must not be lost to the raise"
    assert seen[0].data["intent"] == "stop"
    assert seen[0].data["accepted"] is False
    assert seen[0].data["reason"] == "ConnectionError: the socket is gone"


def test_everything_else_is_delegated() -> None:
    """Verbs probe the transport for things only some bodies have. `stop_error` is what the
    `stop` verb reads to tell "stopped" from "could not deliver a stop"."""
    inner = MockTransport()
    inner.stop_error = "the socket is gone"  # type: ignore[attr-defined]
    logged = LoggedTransport(inner, EventLog())
    assert getattr(logged, "stop_error", None) == "the socket is gone"
    assert getattr(logged, "camera_error", None) is None
    assert logged.name == "mock" and logged.now() == inner.now()


def test_a_wrapper_without_its_privates_raises_attribute_error_not_recursion() -> None:
    logged = LoggedTransport.__new__(LoggedTransport)  # never ran __init__
    with pytest.raises(AttributeError):
        logged._inner  # noqa: B018


async def test_a_nested_verbs_intents_count_for_its_parent_too() -> None:
    """`approach_and` sends nothing itself; every intent comes from the `go_to` it runs. A
    parent that reported zero intents would be the misleading kind of true."""
    from collections import Counter

    logged = LoggedTransport(MockTransport(), EventLog())
    with counting() as parent:
        await logged.send_intent(Intent.move(0.1))
        with counting() as child:
            await logged.send_intent(Intent.move(0.2))
            await logged.stop()
    assert child == Counter({"move": 1, "stop": 1})
    assert parent == Counter({"move": 2, "stop": 1})


async def test_an_intent_sent_outside_any_verb_is_counted_by_nobody() -> None:
    """The heartbeat's stop belongs to no verb: it must not land on whichever tally happens
    to be open in another task."""
    logged = LoggedTransport(MockTransport(), EventLog())
    await logged.stop()  # no `counting()` block: must not raise, must count nowhere
    with counting() as tally:
        pass
    assert tally == {}


# ── rendering ───────────────────────────────────────────────────────────────────────────


def test_a_burst_of_one_intent_kind_becomes_one_line_with_its_ranges() -> None:
    """`go_to` recomputes its twist every 100 ms, so consecutive intents are never identical;
    coalescing by kind is what keeps a 20 s approach from being 200 lines."""
    event_log = EventLog()
    seen = events(event_log)
    for wz in (-0.4, 0.0, 0.35):
        event_log.emit("intent", intent="move", params={"vx": 0.2, "wz": wz}, accepted=True)
    event_log.emit("intent", intent="stop", params={}, accepted=True)
    out = lines(seen)
    assert len(out) == 2
    assert "move x3" in out[0] and "vx 0.2" in out[0] and "wz -0.4..0.35" in out[0]
    assert out[1].split() == ["->", "stop"]  # the label column is padded


def _burst(count: int, *, every: float) -> list[LogEvent]:
    return [
        LogEvent("intent", i * every, {"intent": "move", "params": {"vx": 0.2}, "accepted": True})
        for i in range(count)
    ]


def test_a_long_burst_is_shown_as_it_happens() -> None:
    """The only events during a twenty second approach are its own `move` intents, so a view
    that flushed only on a different event showed the verb starting and then nothing at all
    until it ended."""
    out = lines([*_burst(45, every=0.1), LogEvent("verb_end", 4.5, {"name": "go_to", "ok": True})])
    bursts = [line for line in out if "move x" in line]
    assert len(bursts) == 3, out
    assert sum(int(line.split("move x")[1].split()[0]) for line in bursts) == 45
    for line in bursts:
        assert float(line.split("over ")[1].split(" s")[0]) <= 2.0
    assert "go_to" in out[-1]


def test_a_burst_that_stalls_is_not_split_by_the_count_alone() -> None:
    """A free-running simulator crosses twenty robot seconds in under two wall ones, so the
    count is the bound that matters there."""
    stalled = _burst(150, every=0.0)
    assert len(lines(stalled)) == 1
    assert len(lines(stalled, max_burst=100)) == 2


def test_the_mcp_result_keeps_one_line_per_burst() -> None:
    """That view renders when the call has already ended, so progressive lines would only
    spend the model's line cap saying the same thing ten times."""
    rendered = render_call(_burst(45, every=0.1))
    assert len([line for line in rendered if "move x" in line]) == 1
    assert "move x45" in rendered[0]


def test_a_refused_intent_is_never_folded_into_a_count() -> None:
    event_log = EventLog()
    seen = events(event_log)
    event_log.emit("intent", intent="move", params={"vx": 0.1}, accepted=True)
    event_log.emit("intent", intent="move", params={"vx": 0.1}, accepted=False, reason="too fast")
    out = lines(seen)
    assert len(out) == 2 and "REFUSED: too fast" in out[1]


def test_the_dry_run_gate_shows_a_parameter_the_model_left_null() -> None:
    """`--dry-run` promises every parameter a model would have sent. A parameter it
    explicitly left unset used to render identically to one it never named."""
    event = LogEvent(
        "gate",
        0.0,
        {
            "name": "go_to",
            "gate": "dry_run",
            "outcome": "skipped",
            "reason": "would run go_to, sent nothing",
            "params": {"target": None, "stop_distance": 0.25},
        },
    )
    ((text, _),) = render_lines(event)
    assert "target=null" in text and "stop_distance=0.25" in text


def test_an_intent_line_still_drops_null_parameters() -> None:
    """A twist's `vy=null` on every one of two hundred burst lines is noise."""
    event = LogEvent("intent", 0.0, {"intent": "move", "params": {"vx": 0.1, "vy": None}})
    ((text, _),) = render_lines(event)
    assert "vx=0.1" in text and "vy" not in text


def test_a_note_with_several_lines_is_indented_under_its_label() -> None:
    """A planner's multi-line log ran into the label column and was unreadable: every line
    after the first started at column zero, exactly where a reader looks for the next
    event's label, so a three-line note read as three separate events."""
    text = "planner: it can see the ball\nbearing +12 deg, 0.8 m away\nnext: go_to, then kick"
    ((rendered, style),) = render_lines(LogEvent("note", 0.0, {"text": text}))
    first, *rest = rendered.splitlines()
    assert first.startswith("note") and first.endswith("planner: it can see the ball")
    pad = len(first) - len("planner: it can see the ball")
    assert pad == 8, "the label column"
    assert rest == [" " * pad + "bearing +12 deg, 0.8 m away", " " * pad + "next: go_to, then kick"]
    assert style == "dim"


def test_fmt_value_truncates_long_strings_and_long_reprs() -> None:
    """One `note` carrying a model's whole answer, or a params dict with a frame in it,
    would otherwise be the log. Both are bounded, and both say where they were cut."""
    assert fmt_value("x" * 500) == repr("x" * 57 + "...")
    assert len(fmt_value("x" * 500)) == 62  # 60 characters, and the quotes repr adds
    assert fmt_value("x" * 60) == repr("x" * 60)  # exactly at the limit, untouched

    big = {f"k{i}": list(range(10)) for i in range(20)}
    assert fmt_value(big) == repr(big)[:117] + "..."
    assert len(fmt_value(big)) == 120


def test_a_whole_run_is_timed_in_the_units_a_person_would_say_it_in() -> None:
    """`3847.2 s` is a number the reader has to do arithmetic on before it means anything.
    The minute boundary is in here on both sides because rounding is what picks the band: at
    59.9 the run is still seconds long, and one tenth later it is a minute. The hour boundary
    is the same trick a second time, and it is the one a plain `divmod` gets wrong: an hour
    exactly is `1h 00m`, never `60m 00s`."""
    assert fmt_duration(43.21) == "43.2 s"
    assert fmt_duration(59.94) == "59.9 s"
    # 59.97 printed to one place IS "60.0 s", which is a minute spelled as if it were not one,
    # so the comparison happens after the rounding rather than before it
    assert fmt_duration(59.97) == "1m 00s"
    assert fmt_duration(60.0) == "1m 00s"
    assert fmt_duration(103.4) == "1m 43s"
    assert fmt_duration(3599.0) == "59m 59s"
    assert fmt_duration(3600.0) == "1h 00m"
    assert fmt_duration(3720.0) == "1h 02m"
    assert fmt_duration(7380.0) == "2h 03m"
    # A clock that went backwards is somebody else's bug, and `-0.0 s` in the verdict line
    # would be this one's.
    assert fmt_duration(-5.0) == "0.0 s"


def test_the_price_line_says_where_the_rate_came_from_or_that_there_is_no_rate() -> None:
    """The head of a replay has to account for the cost figure the rest of the run shows,
    including when there is not one: a missing row would leave a reader to guess whether the
    run was free, whether quackd forgot, or whether the model is simply unpriced. The dicts
    here are real `Price.record()` output, because that is what `run_start` carries."""
    assert _price_line(None) == (
        "unpriced: quackd has no rate for this model, so no cost was computed"
    )
    catalogue = Price(input=10.0, output=50.0, cache_read=1.0, cache_write=12.5).record()
    assert _price_line(catalogue) == f"$10/M in, $50/M out (catalogue, checked {PRICES_CHECKED})"
    # A rate the person typed was true on the day they typed it and on no other day, so it
    # carries its source and no date at all.
    typed = Price(3.0, 15.0, 0.3, 3.75, source="--price").record()
    assert _price_line(typed) == "$3/M in, $15/M out (--price)"
    stepper = Price(input=0.042, output=0.0, source="published").record()
    assert _price_line(typed, stepper) == "$3/M in, $15/M out (--price), stepper $0.042/M in"
    assert _price_line(None, stepper) == (
        "unpriced: quackd has no rate for this model, so no cost was computed, stepper $0.042/M in"
    )


def test_a_burst_with_many_distinct_labels_shows_three_and_an_ellipsis() -> None:
    event_log = EventLog()
    seen = events(event_log)
    for i in range(50):
        event_log.emit("intent", intent="do", params={"skill": f"s{i}"}, accepted=True)
    (out,) = lines(seen)
    assert "s0'/'s1'/'s2'..." in out.replace('"', "'") or "s0" in out
    assert "..." in out


def test_a_write_that_fails_keeps_the_burst_for_the_next_flush() -> None:
    """`flush` used to clear the pending burst before writing it, so a write that raised
    lost the intents entirely."""
    written: list[str] = []
    failed = {"once": True}

    def write(text: str, _style: str) -> None:
        if failed["once"]:
            failed["once"] = False
            raise RuntimeError("the terminal went away")
        written.append(text)

    view = LineLog(write)
    view(LogEvent("intent", 0.0, {"intent": "move", "params": {"vx": 0.1}, "accepted": True}))
    with pytest.raises(RuntimeError):
        view.flush()
    view.flush()
    assert len(written) == 1 and "move" in written[0]


def test_a_renderer_bug_never_turns_a_result_into_an_internal_error() -> None:
    """`render_call` runs outside the event_log, so nothing swallows its exceptions: a
    formatting error would have failed the MCP tool call instead of answering it."""
    broken = LogEvent("verb_end", 0.0, {"elapsed_s": "soon", "intents": {"move": 1}})
    rendered = render_call([broken])
    assert len(rendered) == 1 and "could not be rendered" in rendered[0]


def test_the_llm_line_shows_thinking_the_call_and_the_tokens() -> None:
    event = LogEvent(
        "llm",
        0.0,
        {
            "step": 2,
            "text": "going for it",
            "thinking": "the ball is 0.4 m away, so walk first",
            "tool_calls": [{"name": "go_to", "arguments": {"target": "ball"}}],
            "usage": {"input_tokens": 1200, "output_tokens": 40},
            "usage_total": {"input_tokens": 5000, "output_tokens": 130},
            "latency_s": 1.25,
            "stop_reason": "tool_use",
        },
    )
    out = [text for text, _ in render_lines(event)]
    assert any("the ball is 0.4 m away" in line for line in out)
    assert any("go_to(target='ball')" in line for line in out)
    assert any("in=1200 out=40" in line and "latency=1.2 s" in line for line in out)


def test_the_tokens_line_prices_the_call_when_it_can() -> None:
    """Four numbers a reader needs and cannot get anywhere else: how much of the prompt came
    back out of the cache, how much went into it, what this one call cost, and what the run
    has spent so far. The run total goes inside the parenthesis that already holds the running
    token counts, because it answers the same question those do."""
    event = LogEvent(
        "llm",
        0.0,
        {
            "step": 3,
            "tool_calls": [],
            "usage": {
                "input_tokens": 1631,
                "output_tokens": 16,
                "cache_read_tokens": 1024,
                "cache_write_tokens": 512,
            },
            "usage_total": {"input_tokens": 9812, "output_tokens": 96},
            "cost_usd": 0.0051,
            "cost_usd_total": 0.0309,
            "latency_s": 0.04,
            "stop_reason": "tool_use",
        },
    )
    tokens = [text for text, _ in render_lines(event)][-1]
    assert tokens == (
        "tokens  in=1631 out=16 cached=1024 cache_write=512"
        " (run total in=9812 out=96 $0.0309) latency=0.0 s cost=$0.0051 stop=tool_use"
    )
    # `in=` stays the whole prompt, cached slice included: it is the number the vendor bills
    # against, and a reader who subtracted the cache from it would be wrong about both.
    assert "in=1631" in tokens and "cached=1024" in tokens


def test_a_tokens_line_from_before_the_money_renders_exactly_as_it_always_did() -> None:
    """Every transcript recorded before this change replays through this renderer: `quackd log`
    reads run directories written months ago, and tests/golden/log_lines.json holds the lines
    those runs printed. A record with no cache buckets, no cost and no running cost has to come
    out of here byte for byte, which is why every new piece is written only when the key is
    there."""
    event = LogEvent(
        "llm",
        0.0,
        {
            "step": 2,
            "tool_calls": [{"name": "go_to", "arguments": {"target": "ball"}}],
            "usage": {"input_tokens": 1200, "output_tokens": 40},
            "usage_total": {"input_tokens": 5000, "output_tokens": 130},
            "latency_s": 1.25,
            "stop_reason": "tool_use",
        },
    )
    tokens = [text for text, _ in render_lines(event)][-1]
    assert (
        tokens == "tokens  in=1200 out=40 (run total in=5000 out=130) latency=1.2 s stop=tool_use"
    )


def test_a_turn_a_fallback_answered_says_which_model_did_at_the_end_of_its_tokens_line() -> None:
    """The one field that changes what the rest of the line means: the cost on it was priced at
    the rate of the model asked for, and a different model answered."""
    event = LogEvent(
        "llm",
        0.0,
        {
            "step": 2,
            "tool_calls": [{"name": "go_to", "arguments": {"target": "ball"}}],
            "usage": {"input_tokens": 1200, "output_tokens": 40},
            "usage_total": {"input_tokens": 5000, "output_tokens": 130},
            "latency_s": 1.25,
            "stop_reason": "tool_use",
            "served_by": "claude-opus-4-8",
        },
    )
    tokens = [text for text, _ in render_lines(event)][-1]
    assert tokens == (
        "tokens  in=1200 out=40 (run total in=5000 out=130) latency=1.2 s stop=tool_use"
        " served_by=claude-opus-4-8"
    )


def test_the_stepper_line_carries_its_tokens_and_its_fraction_of_a_cent() -> None:
    """The whole argument for a stepper is the ratio between what one of its questions costs
    and what the model call it stands in for costs, so both numbers ride in the parenthesis
    that already holds the seconds. A `~` marks a turn the decision LLM did not count for
    itself and quackd had to estimate from the characters it sent, and it goes on both numbers
    because the cost is derived from the count."""

    def line(**extra: Any) -> str:
        data = {
            "gate": "taken",
            "choice": "kick",
            "confidence": 0.93,
            "floor": 0.7,
            "latency_s": 0.11,
            **extra,
        }
        ((text, _),) = render_lines(LogEvent("decision", 0.0, data))
        return text

    counted = {"usage": {"input_tokens": 527, "output_tokens": 3}, "cost_usd": 0.000022}
    assert "(0.11 s, 527 tok $0.000022)" in line(**counted, usage_estimated=False)
    assert "(0.11 s, ~527 tok ~$0.000022)" in line(**counted, usage_estimated=True)
    # The gates that never reach the network owe nothing, and a `0 tok` on them would read as
    # a question that was asked and came back empty. The gutter is eight columns wide
    # (`_LABEL`), so the six letters of `decide` are followed by exactly two spaces.
    assert line() == "decide  kick 0.93 >= 0.70 (0.11 s)"
    assert line(gate="not_offered") == "decide  not_offered, to the model (0.11 s)"


def test_a_declined_question_is_named_on_the_stepper_line_and_wants_attention() -> None:
    """A question the decision LLM declined is neither a failure nor one of the quiet handovers:
    its vendor's policy would not judge this state, and a reader watching a shadow run wants to
    see how often that happens and on which question. So the line names what was declined, in
    yellow, with the mark a missed floor has, and keeps the bill a request that went out has."""

    def lines(**extra: Any) -> list[Any]:
        data = {
            "gate": "refused",
            "choice": "stop",
            "confidence": 0.97,
            "probabilities": {"stop": 0.97, "escalate": 0.02},
            "latency_s": 0.4,
            **extra,
        }
        return render_events(LogEvent("decision", 0.0, data))

    billed = {"usage": {"input_tokens": 42, "output_tokens": 0}, "cost_usd": 0.0000042}
    first, runners_up = lines(refused=["done"], **billed)
    assert first.label == "decide" and first.style == "yellow" and first.mark == "warn"
    assert first.body == "refused to answer done, to the model (0.40 s, 42 tok $0.000004)"
    # what it did answer is still worth reading beside it
    assert runners_up.label == "decide?" and runners_up.body == "escalate 0.02"
    (two, _rest) = lines(refused=["next_verb", "need_human"])
    assert two.body == "refused to answer next_verb and need_human, to the model (0.40 s)"
    # and more than two as a sentence lists them, not "a and b and c"
    (three, _rest) = lines(refused=["next_verb", "done", "need_human"])
    assert three.body == "refused to answer next_verb, done and need_human, to the model (0.40 s)"
    # a record without the names, which quackd never writes, still says which gate it was
    (bare, _rest) = lines()
    assert bare.body == "refused, to the model (0.40 s)" and bare.mark == "warn"


def test_long_thinking_is_cut_with_a_pointer_to_the_transcript() -> None:
    event = LogEvent("llm", 0.0, {"thinking": "x" * 5000, "tool_calls": [], "usage": {}})
    out = [text for text, _ in render_lines(event, thinking_chars=100)]
    assert "+4900 chars in transcript.jsonl" in out[0] and len(out[0]) < 400
    full = [text for text, _ in render_lines(event, thinking_chars=None)]
    assert "transcript.jsonl" not in full[0] and len(full[0]) > 4000
    none = [text for text, _ in render_lines(event, thinking_chars=0)]
    assert not any("xxx" in line for line in none)


def test_an_llm_call_that_failed_is_a_line_too() -> None:
    event = LogEvent("llm", 0.0, {"error": "ProviderError: rate limited", "latency_s": 3.0})
    ((text, style),) = render_lines(event)
    assert "rate limited" in text and style == "red"


def test_llm_request_says_when_there_are_more_images_than_messages() -> None:
    """A body with two cameras puts two pictures in one exchange, so the old single count
    could no longer stand for both. It is still one number wherever they agree, which is
    every one-camera run, and the second is only spelled out where they differ.

    The third case is why the fallback exists. A transcript recorded before the loop split
    the count carries `images` alone, and there it meant exchanges: `quackd log` replays
    those run directories and `tests/golden/log_lines.json` holds the lines they printed,
    so an old event has to render the string it always did.
    """

    def line(**counts: Any) -> str:
        data = {"step": 2, "messages": 7, "provider": "openai", "model": "gpt-5", **counts}
        ((text, _),) = render_lines(LogEvent("llm_request", 0.0, data))
        return text

    assert line(images=4, with_image=2) == (
        "llm>    step 2: 7 messages (2 with image, 4 images) to openai gpt-5"
    )
    assert line(images=2, with_image=2) == (
        "llm>    step 2: 7 messages (2 with image) to openai gpt-5"
    )
    assert line(images=1) == "llm>    step 2: 7 messages (1 with image) to openai gpt-5", (
        "an old transcript carries no with_image and must replay line for line"
    )


def test_a_gate_says_which_rule_refused_and_why() -> None:
    event = LogEvent(
        "gate",
        0.0,
        {"name": "kick", "gate": "allowlist", "outcome": "refused", "reason": "not allowed here"},
    )
    ((text, style),) = render_lines(event)
    assert "allowlist: refused not allowed here" in text and style == "red"


def test_the_dry_run_gate_shows_what_would_have_been_sent() -> None:
    event = LogEvent(
        "gate",
        0.0,
        {
            "name": "walk",
            "gate": "dry_run",
            "outcome": "skipped",
            "reason": "would run walk, sent nothing",
            "params": {"vx": 0.15, "duration_s": 1.0},
        },
    )
    ((text, _),) = render_lines(event)
    assert "vx=0.15" in text and "duration_s=1" in text


def test_the_verb_end_line_counts_the_intents_and_the_seconds() -> None:
    event = LogEvent(
        "verb_end",
        0.0,
        {
            "name": "go_to",
            "ok": True,
            "outcome": "ok",
            "summary": "reached the ball",
            "elapsed_s": 12.5,
            "intents": {"move": 120, "stop": 1},
        },
    )
    ((text, style),) = render_lines(event)
    assert "go_to ok: reached the ball (12.5 s, 121 intents)" in text and style == "green"


def test_the_verb_end_line_shows_both_clocks_only_when_they_disagree() -> None:
    """A free-running simulator crosses twenty of the robot's seconds in one of ours. One
    line saying `1.4 s` for that walk is wrong, and two numbers on every sub-second verb is
    noise, so both a half second and a fifth have to separate them."""

    def line(**extra: Any) -> str:
        data = {"name": "go_to", "ok": True, "outcome": "ok", "summary": "there", **extra}
        ((text, _),) = render_lines(LogEvent("verb_end", 0.0, data))
        return text

    assert "20.0 s sim, 1.4 s wall" in line(elapsed_s=1.4, transport_s=20.0, clock="sim")
    assert "(1.0 s," in line(elapsed_s=1.0, transport_s=1.1, clock="sim")  # too close to say
    assert "(1.4 s," in line(elapsed_s=1.4, transport_s=20.0)  # no clock: hardware, one number


def test_a_burst_spans_the_robots_clock_when_it_has_one() -> None:
    """`move x200 over 1.4 s` implies 140 Hz to a reader when the commanded rate was 10."""
    event_log = EventLog()
    seen = events(event_log)
    for wall, robot in ((0.0, 0.0), (1.4, 20.0)):
        event_log.emit("intent", intent="move", params={"vx": 0.2}, accepted=True, robot_t=robot)
        object.__setattr__(seen[-1], "t", wall)
    (out,) = lines(seen)
    assert "over 20.0 s" in out


def test_the_loops_own_verb_record_renders_nothing() -> None:
    """It is the same call as `verb_end`, kept in the transcript for the readers that pin it."""
    assert render_lines(LogEvent("verb", 0.0, {"name": "kick", "ok": True})) == []
    assert render_lines(LogEvent("frame", 0.0, {"path": "frames/0001.png"})) == []
    assert render_lines(LogEvent("run_end", 0.0, {"outcome": "success"})) == []


def test_run_start_shows_the_prompt_once_and_can_be_asked_not_to() -> None:
    event = LogEvent(
        "run_start",
        0.0,
        {
            "duck": "find-and-kick",
            "provider": "anthropic",
            "model": "claude-opus-5",
            "transport": "sim2d",
            "adapter": "microduck",
            "tools": ["walk", "kick"],
            "system_prompt": "You are the brain of a duck.\nRules follow.",
            "connect_s": 0.05,
        },
    )
    with_prompt = [text for text, _ in render_lines(event)]
    assert any("You are the brain of a duck." in line for line in with_prompt)
    assert any("robot=microduck:sim2d" in line for line in with_prompt)
    without = [text for text, _ in render_lines(event, prompt=False)]
    assert not any("brain of a duck" in line for line in without)


# ── the console ─────────────────────────────────────────────────────────────────────────


def console_log(**kwargs: Any) -> tuple[ConsoleLog, io.StringIO]:
    buffer = io.StringIO()
    console = Console(file=buffer, width=200, force_terminal=False, no_color=True)
    kwargs.setdefault("thinking_chars", None)
    return ConsoleLog(console, **kwargs), buffer


def test_square_brackets_in_what_the_model_wrote_survive_verbatim() -> None:
    """Rich reads `[dim]` as markup and raises on an unpaired closing tag. Every line here
    carries text a model or a robot wrote, so none of it may be parsed as markup."""
    view, buffer = console_log()
    view(
        LogEvent(
            "llm",
            0.0,
            {
                "thinking": "[/think] and [dry-run] and [bold]",
                "text": "the ball is [behind] the sofa",
                "tool_calls": [],
                "usage": {},
            },
        )
    )
    out = buffer.getvalue()
    assert "[/think]" in out and "[dry-run]" in out and "[bold]" in out
    assert "[behind]" in out


def test_the_console_flushes_a_pending_burst_before_the_next_line() -> None:
    view, buffer = console_log()
    for _ in range(3):
        view(LogEvent("intent", 0.0, {"intent": "move", "params": {"vx": 0.1}, "accepted": True}))
    view(
        LogEvent(
            "verb_end", 0.4, {"name": "move", "ok": True, "outcome": "ok", "summary": "walked"}
        )
    )
    out = buffer.getvalue().splitlines()
    assert "move x3" in out[0] and "walked" in out[1]


def test_fan_out_feeds_every_sink_and_one_bad_view_never_starves_the_others() -> None:
    """The loop takes a single observer and a run wants two: the narration and the status
    line that says what it is waiting for. A console that raises must not take the status
    with it, and the EventLog must still count exactly one drop for the event."""
    seen_a: list[str] = []
    seen_b: list[str] = []

    def broken(_event: LogEvent) -> None:
        raise ValueError("a terminal that cannot print")

    sink = fan_out(lambda e: seen_a.append(e.kind), None, broken, lambda e: seen_b.append(e.kind))
    event_log = EventLog(observers=[sink])
    event_log.emit("llm", text="hello")
    assert seen_a == ["llm"] and seen_b == ["llm"], "a raise stopped a later sink"
    assert event_log.dropped == 1, "the failure has to reach the EventLog, once"


def test_fan_out_of_nothing_is_a_sink_that_does_nothing() -> None:
    fan_out()(LogEvent("llm", 0.0, {}))  # must not raise


def test_every_kind_of_moment_keeps_the_mark_its_glyph_is_chosen_from() -> None:
    """`mark` is the only thing the terminal reads to decide what a line looks like, and the
    golden cannot see it: it freezes `render_lines`, which throws the mark away. Swapping two
    of these would turn every refusal into a tick and no other test would notice."""
    from tests.golden.log_cases import events

    marks = {
        name: [line.mark for line in render_events(event) if line.mark] for name, event in events()
    }
    assert marks["verb_start"] == ["start"]
    assert marks["intent_one"] == ["send"] and marks["intent_refused"] == ["fail"]
    assert marks["verb_end_ok"] == ["ok"] and marks["verb_end_fail"] == ["fail"]
    assert marks["verb_end_preempted"] == ["other"], "a handover is not a fault"
    assert marks["gate_refused"] == ["fail"] and marks["gate_allowed"] == ["warn"]
    # a person's yes is a note and their no wants attention. Neither is a failure: whoever
    # acted on the no records that separately, with the mark a refusal deserves.
    assert marks["prompt_yes"] == ["note"] and marks["prompt_no"] == ["warn"]
    assert marks["declare_success"] == ["ok"] and marks["declare_failure"] == ["fail"]
    assert marks["llm_error"] == ["fail"] and marks["llm_no_tool_call"] == ["warn"]
    assert marks["observation_error"] == ["fail"] and marks["enforce"] == ["warn"]
    assert marks["flock_claim"] == ["flock"] and marks["member_end"] == ["end"]
    assert marks["note"] == ["note"] and marks["memory"] == ["note"]
    assert marks["assess_feasible"] == ["ok"], "a verdict that clears the way reads like one"
    assert marks["assess_infeasible"] == ["warn"], "nothing broke: this body cannot"
    assert marks["assess_uncertain_human_no"] == ["warn"]
    assert marks["assess_invalid"] == ["fail"]
    assert marks["tool_result"] == ["ok"] and marks["tool_result_sim"] == ["fail"]
    # and nothing invents a mark the glyph table has no field for
    from quackd import ui

    for name, found in marks.items():
        assert all(m in ui.MARKS for m in found), (name, found)


# ── the terminal view ───────────────────────────────────────────────────────────────────


def narrow_log(**kwargs: Any) -> tuple[ConsoleLog, io.BytesIO]:
    """A view on a Windows codepage: what `2> run.log` gives you there."""
    raw = io.BytesIO()
    console = Console(
        file=io.TextIOWrapper(raw, encoding="cp1252", errors="replace"),
        width=200,
        force_terminal=False,
    )
    return ConsoleLog(console, **kwargs), raw


def shown(raw: io.BytesIO, view: ConsoleLog) -> str:
    view.console.file.flush()
    return raw.getvalue().decode("cp1252")


def test_the_terminal_puts_the_arrow_in_the_gutter_and_a_word_in_the_column() -> None:
    """The plain renderer's `->` is a contract with a model. A person gets the arrow as a
    glyph and the column says what it stood for."""
    view, buffer = console_log()
    view(LogEvent("verb_start", 0.0, {"name": "go_to", "params": {"target": "ball"}}))
    view(LogEvent("intent", 0.1, {"intent": "move", "params": {"vx": 0.2}, "accepted": True}))
    view(
        LogEvent(
            "verb_end", 0.2, {"name": "go_to", "ok": True, "outcome": "ok", "summary": "there"}
        )
    )
    out = buffer.getvalue()
    assert "▶  verb    go_to(target='ball')" in out
    assert "→  send    move(vx=0.2)" in out
    assert "✓  result  go_to ok: there" in out
    assert "->" not in out and "<-" not in out, "the arrows are glyphs here, not text"


def test_a_failure_is_a_shape_as_well_as_a_colour() -> None:
    view, buffer = console_log()
    view(LogEvent("gate", 0.0, {"gate": "allowlist", "outcome": "refused", "reason": "no"}))
    view(
        LogEvent(
            "verb_end", 0.1, {"name": "kick", "outcome": "fail", "summary": "missed", "ok": False}
        )
    )
    view(LogEvent("verb_end", 0.2, {"name": "s", "outcome": "preempted", "summary": "role"}))
    out = buffer.getvalue()
    assert "✗  gate" in out and "✗  result  kick FAIL" in out
    assert "•  result  s PREEMPTED" in out, "a handover is not a fault and must not look like one"


def test_every_glyph_becomes_ascii_on_a_stream_that_cannot_carry_it() -> None:
    """A redirected stderr on Windows is cp1252, and this is exactly the output people
    redirect. ADR-0029 protected that with ASCII everywhere; it is protected here by asking
    the stream."""
    view, raw = narrow_log()
    view(LogEvent("verb_start", 0.0, {"name": "go_to", "params": {}}))
    view(LogEvent("intent", 0.1, {"intent": "move", "params": {"vx": 0.2}, "accepted": True}))
    done = {"name": "go_to", "ok": True, "outcome": "ok", "summary": "x"}
    view(LogEvent("verb_end", 0.2, done))
    out = shown(raw, view)
    assert out.isascii() and "?" not in out
    assert ">  verb" in out and "-> send" in out and "+  result" in out


def test_what_a_robot_wrote_is_respelled_rather_than_lost() -> None:
    """`bearing 28° left` arrived as `bearing 28? left` on a codepage without a degree sign.
    A stand-in that says the same thing is strictly better than a question mark."""
    view, raw = narrow_log()
    view(LogEvent("note", 0.0, {"text": "ball at bearing 28° left ±2°"}))
    out = shown(raw, view)
    assert "28 deg left +/-2 deg" in out and "?" not in out


def test_each_step_is_ruled_off_and_the_budget_is_only_said_once() -> None:
    """The loop writes `[step 3/40 · step 3/40, llm calls ...]` at the top of every
    observation: the one line saying where the run is up to, buried in a paragraph, and
    saying the step twice."""
    view, buffer = console_log()
    text = "[step 3/40 · step 3/40, llm calls 3/40, 0.1/5 min]\nstate: posture=standing"
    view(LogEvent("observation", 0.0, {"step": 3, "text": text}))
    out = buffer.getvalue()
    assert "step 3/40, llm calls 3/40, 0.1/5 min" in out
    assert out.count("step 3/40") == 1, "the step was announced twice"
    assert "state: posture=standing" in out
    assert "[step" not in out, "the header became the rule and must not also be a line"


def test_an_observation_that_failed_is_a_line_not_a_rule() -> None:
    view, buffer = console_log()
    view(LogEvent("observation", 0.0, {"error": "camera timed out"}))
    assert "ERROR camera timed out" in buffer.getvalue()


def test_a_flock_member_is_never_given_a_rule_of_its_own() -> None:
    """Three members narrate at once; a rule each would be three rules per step and none of
    them would mean the run had moved on."""
    view, buffer = console_log(prefix="duck-1  ")
    view(LogEvent("observation", 0.0, {"text": "[step 1/9 · llm calls 1/9]\nstate: up"}))
    view(LogEvent("verb_start", 0.1, {"name": "kick", "params": {}}))
    out = buffer.getvalue()
    assert "───" not in out and "[step 1/9" in out, "the budget stays in the member's own line"
    assert all(line.startswith("duck-1  ") for line in out.splitlines() if line.strip())


def test_the_system_prompt_is_a_block_between_two_rules() -> None:
    view, buffer = console_log()
    view(
        LogEvent(
            "run_start",
            0.0,
            {"duck": "d", "transport": "mock", "system_prompt": "line one\n\nline three"},
        )
    )
    out = buffer.getvalue()
    assert "system prompt, 20 chars, 3 lines" in out
    # the exact lines: a substring check passes on any indent at all, and the point of the
    # block is that seventy lines of somebody else's prose do not eat the terminal's width
    lines = out.splitlines()
    assert "   line one" in lines and "   line three" in lines
    assert "" in lines, "a blank line carries no padding: that is what a redirect diffs on"
    assert out.rstrip().endswith("─" * 10), "the block is closed off so the run visibly starts"


def test_the_system_prompt_is_respelled_for_the_stream_like_every_other_line() -> None:
    view, raw = narrow_log()
    prompt = "## Rules (enforced by the executor — not optional)"
    view(LogEvent("run_start", 0.0, {"duck": "d", "transport": "mock", "system_prompt": prompt}))
    out = shown(raw, view)
    assert "executor - not optional" in out and "?" not in out


def test_what_a_person_was_asked_is_a_line_and_not_the_rule_the_prompt_gets() -> None:
    """`_show` draws any line labelled `prompt` as a section rule, which is the rule the
    system prompt opens a run with. The event that records a person's answer is a `prompt`
    event too, so its line came out as that rule: no question, no answer, nothing."""
    view, buffer = console_log()
    view(
        LogEvent(
            "prompt", 0.0, {"what": "confirm", "question": "run kick(power=0.5)?", "answer": True}
        )
    )
    out = buffer.getvalue()
    assert "·  asked   confirm: run kick(power=0.5)? -> yes" in out
    assert "─" not in out, "a person's answer is a line of its own, not a section rule"


def test_a_yes_and_a_no_do_not_come_out_looking_like_each_other() -> None:
    """Both were the same grey rule. Whether the person said yes is the first thing anybody
    reading the terminal afterwards wants from the line, and here it is the glyph."""
    put = {"what": "confirm", "question": "run kick(power=0.5)?"}
    (yes,) = render_events(LogEvent("prompt", 0.0, {**put, "answer": True}))
    (no,) = render_events(LogEvent("prompt", 0.0, {**put, "answer": False}))
    assert (yes.mark, yes.style) == ("note", "cyan")
    assert (no.mark, no.style) == ("warn", "yellow")
    assert no.style != "red", "the person answered; the refusal is the gate's line to draw"
    view, buffer = console_log()
    view(LogEvent("prompt", 0.0, {**put, "answer": True}))
    view(LogEvent("prompt", 0.1, {**put, "answer": False}))
    said_yes, said_no = buffer.getvalue().splitlines()
    assert said_yes.startswith("·  asked") and said_yes.endswith("-> yes")
    assert said_no.startswith("⚠  asked") and said_no.endswith("-> no")


def test_a_refused_hold_is_not_logged_as_held() -> None:
    """A take-hold is recorded under the stage it was trying for, `held`, whatever came of it,
    and the line used to print that stage as its first word. So a take-hold that left torque off
    under a joint placed past its travel came out as "held: ..., so quackd left torque off", to
    a person holding the arm and reading the first word. A refused hold and a refused release
    say so in that word, and a hold that took is unchanged."""
    joints = {"wrist_flex": 12.0}
    (refused,) = render_events(
        LogEvent(
            "hand_off", 0.0, {"stage": "held", "how": "refused", "reason": "r", "joints": joints}
        )
    )
    assert refused.body.startswith("hold refused: r"), refused.body
    assert refused.style == "yellow"
    (release,) = render_events(
        LogEvent("hand_off", 0.0, {"stage": "released", "how": "refused", "reason": "r"})
    )
    assert release.body == "release refused: r", release.body
    (held,) = render_events(
        LogEvent("hand_off", 0.0, {"stage": "held", "how": "held", "reason": "r", "joints": joints})
    )
    assert (held.body, held.style) == ("held: r (wrist_flex 12)", "cyan")


def test_a_refused_hold_never_prints_a_joint_inside_the_travel_its_reason_puts_it_outside() -> None:
    """The refusal for a joint placed past its travel prints the reading so that it is never
    inside the travel the same sentence gives: a hundredth past an edge is printed as it is,
    because its tenth is the edge. The line then went on to list every joint in whole degrees,
    which put that joint at the edge, inside the travel its own reason had just said it was
    outside. A hold refused over joints outside their travel (`outside` in the event) ends on
    its reason; the joints stay in the event. The travel and the reading here are made up for
    this test."""
    edge = 40.0
    reading = edge + 0.04
    reason = f"wrist_flex reads {reading!r}, outside its calibrated travel of -{edge}..{edge}"
    event = {
        "stage": "held",
        "how": "refused",
        "reason": reason,
        "joints": {"wrist_flex": reading},
        "outside": ["wrist_flex"],
    }
    (line,) = render_events(LogEvent("hand_off", 0.0, event))
    assert line.body == f"hold refused: {reason}", line.body
    assert f"wrist_flex {edge:.0f}" not in line.body, line.body


def test_a_hold_refused_because_the_arm_moved_still_says_where_it_is_holding() -> None:
    """An arm that moved as torque came on is refused with a reason that names the joint that
    moved furthest and by how much, and ends "it is holding where it is now". It names no
    reading, so the joint list after it is the only place the line says where "now" is. The
    list was dropped from every refused hold, this one too, to keep a joint read a hair past
    its travel from being rounded onto the edge; that happens only where the reason carries
    readings, which the event says with `outside`, and a slip has none. Poses and reason are
    made up for this test."""
    joints = {"elbow_flex": 31.6, "wrist_flex": -7.2}
    reason = "the arm moved as torque came on (elbow_flex by 9 degrees), so it holds where it is"
    event = {"stage": "held", "how": "refused", "reason": reason, "joints": joints}
    (line,) = render_events(LogEvent("hand_off", 0.0, event))
    assert line.body == f"hold refused: {reason} (elbow_flex 32, wrist_flex -7)", line.body


def test_the_system_prompt_is_still_the_one_thing_drawn_as_a_rule() -> None:
    """The label was narrowed and the branch left alone, so the run still opens with the
    rule, the block and the rule that closes it, and nothing else in the run gets one."""
    view, buffer = console_log()
    view(
        LogEvent(
            "run_start",
            0.0,
            {"duck": "d", "transport": "mock", "system_prompt": "line one\nline two"},
        )
    )
    view(LogEvent("prompt", 0.1, {"what": "confirm", "question": "run kick()?", "answer": True}))
    out = buffer.getvalue()
    ruled = [line for line in out.splitlines() if "─" in line]
    assert len(ruled) == 2, ruled
    assert "system prompt, 17 chars, 2 lines" in ruled[0]
    assert not ruled[1].strip("─"), "the block is closed off and the run visibly starts after"
    assert "asked   confirm: run kick()? -> yes" in out


def test_the_asked_label_sits_in_the_column_the_other_labels_sit_in() -> None:
    """Eight characters wide, like `result` and `enforce`. A longer word would push one
    line's text out of line with every other line of the run."""
    view, buffer = console_log()
    view(LogEvent("verb_start", 0.0, {"name": "kick", "params": {}}))
    view(LogEvent("prompt", 0.1, {"what": "confirm", "question": "run kick()?", "answer": True}))
    verb, asked = buffer.getvalue().splitlines()
    assert verb.index("kick()") == asked.index("confirm:")
    # and the plain reader gets the same padded gutter it has been handed all along
    ((text, _),) = render_lines(
        LogEvent("prompt", 0.0, {"what": "acknowledge", "question": "sure?", "answer": False})
    )
    assert text == "asked   acknowledge: sure? -> no"


def test_a_live_run_does_not_repeat_the_header_the_cli_just_printed() -> None:
    view, buffer = console_log()
    view(
        LogEvent(
            "run_start",
            0.0,
            {
                "duck": "d",
                "provider": "fake",
                "transport": "mock",
                "connect_s": 0.5,
                "tools": ["a"],
            },
        )
    )
    out = buffer.getvalue()
    assert "connected in 0.50 s" in out
    assert "tools   a" in out
    assert "provider=fake" not in out, "the panel in front of this already said it"


def test_a_replay_introduces_the_run_because_nothing_else_did() -> None:
    view, buffer = console_log(header=True)
    view(
        LogEvent(
            "run_start",
            0.0,
            {
                "duck": "find-and-kick",
                "provider": "anthropic",
                "model": "claude-opus-5",
                "adapter": "microduck",
                "transport": "sim2d",
                "tools": ["kick"],
                "connect_s": 0.5,
                "memory": {"notes": 3, "episodes": 5},
            },
        )
    )
    out = buffer.getvalue()
    for needle in (
        "find-and-kick",
        "anthropic (claude-opus-5)",
        "microduck:sim2d",
        "3 notes, 5 earlier runs",
        "connected in 0.50 s",
    ):
        assert needle in out, needle


def test_a_replay_says_when_the_run_happened_what_it_was_called_and_its_rate() -> None:
    """A replay has no CLI header in front of it, and until the record carried a wall clock
    the only answer to "when was this" was the directory name, which gets renamed and copied.

    The run name row is there only when somebody typed one. The price row has three states and
    they are not two: a rate that was used, a `price` of null for a run quackd could not cost
    and which needs the panel to say so, and no `price` key at all for a transcript recorded
    before there were prices. Telling that third reader their run was "unpriced" would be
    describing this release rather than their run, so it gets no row."""
    started = "2026-09-21T15:44:01.507Z"
    price = Price(3.0, 15.0, 0.3, 3.75, source="--price").record()

    def panel(**extra: Any) -> str:
        view, buffer = console_log(header=True)
        data = {"duck": "find-and-kick", "provider": "fake", "transport": "sim2d", **extra}
        view(LogEvent("run_start", 0.0, data))
        return buffer.getvalue()

    named = panel(started_at=started, run_name="Example 1", price=price)
    assert "started   2026-09-21 15:44:01.507 UTC" in named, "the T and the Z are machine spelling"
    assert "run name  Example 1" in named, "the name as it was typed, not the slug"
    assert "price     $3/M in, $15/M out (--price)" in named

    anonymous = panel(started_at=started, price=None)
    assert "run name" not in anonymous, "an empty row would read as a run called nothing"
    assert "price     unpriced: quackd has no rate for this model" in anonymous

    before = panel(started_at=started)
    assert "price" not in before, (
        "a transcript from before there were prices has no rate to report and was not "
        "'unpriced': every published transcript in docs/assets replays through this"
    )


def test_the_terminal_view_never_reads_what_a_model_wrote_as_markup() -> None:
    """The same promise the plain view makes, made again by the renderer that replaced it."""
    view, buffer = console_log()
    view(
        LogEvent(
            "llm",
            0.0,
            {
                "thinking": "[/think] and [dry-run] and [bold]",
                "text": "the ball is [behind] the sofa",
                "tool_calls": [],
                "usage": {},
            },
        )
    )
    out = buffer.getvalue()
    for tag in ("[/think]", "[dry-run]", "[bold]", "[behind]"):
        assert tag in out, tag


# ── capturing one call (the MCP server) ─────────────────────────────────────────────────


async def test_two_concurrent_calls_never_see_each_others_events() -> None:
    """The MCP SDK runs every tool call as its own task. A buffer on the session would put
    one call's intents in the other call's result."""
    event_log = EventLog(observers=[capture_sink])

    async def call(name: str, delay: float) -> list[str]:
        with capturing() as seen:
            event_log.emit("verb_start", name=name)
            await asyncio.sleep(delay)
            event_log.emit("intent", intent=name, params={}, accepted=True)
            await asyncio.sleep(delay)
            event_log.emit("verb_end", name=name, ok=True, outcome="ok", summary="done")
            return [e.data.get("name") or e.data.get("intent") for e in seen]

    slow, fast = await asyncio.gather(call("go_to", 0.02), call("quack", 0.001))
    assert set(slow) == {"go_to"} and set(fast) == {"quack"}


def test_nothing_is_captured_outside_a_call() -> None:
    EventLog(observers=[capture_sink]).emit("note", text="the heartbeat failed")  # must not raise


def test_render_call_is_short_plain_lines() -> None:
    event_log = EventLog(observers=[capture_sink])
    with capturing() as seen:
        event_log.emit("tool_call", tool="robot_run_verb", robot="duck", verb="go_to", params={})
        event_log.emit("verb_start", name="go_to", params={"target": "ball"}, source="mcp")
        for wz in (0.1, 0.2):
            event_log.emit("intent", intent="move", params={"vx": 0.2, "wz": wz}, accepted=True)
        event_log.emit(
            "verb_end",
            name="go_to",
            ok=True,
            outcome="ok",
            summary="reached the ball",
            elapsed_s=1.0,
            intents={"move": 2},
        )
        event_log.emit(
            "tool_result", tool="robot_run_verb", ok=True, elapsed_s=1.1, budget="step 1/40"
        )
    rendered = render_call(seen)
    assert any("go_to(target='ball') from mcp" in line for line in rendered)
    assert any("move x2" in line for line in rendered)
    assert any("reached the ball" in line for line in rendered)
    assert any("step 1/40" in line for line in rendered)
    assert all(isinstance(line, str) for line in rendered)


def test_a_long_log_is_capped_and_says_what_it_cut() -> None:
    capped = cap_lines([f"line {i}" for i in range(100)], limit=10, head=3)
    assert len(capped) == 10
    assert capped[:3] == ["line 0", "line 1", "line 2"]
    assert "91 more lines" in capped[3] and "stderr" in capped[3]
    assert capped[-1] == "line 99"
    short = ["a", "b"]
    assert cap_lines(short, limit=10) == short


# ── the switch ──────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "on"),
    [
        (None, True),
        ("", True),
        ("1", True),
        ("yes", True),
        ("0", False),
        ("false", False),
        ("No", False),
        ("off", False),
    ],
)
def test_the_env_switch(monkeypatch: pytest.MonkeyPatch, value: str | None, on: bool) -> None:
    if value is None:
        monkeypatch.delenv("QUACKD_LOG", raising=False)
    else:
        monkeypatch.setenv("QUACKD_LOG", value)
    assert log_enabled_default() is on


@pytest.mark.parametrize(
    ("value", "limit"), [("", 2000), ("all", None), ("0", 0), ("500", 500), ("nonsense", 2000)]
)
def test_how_much_thinking_the_console_shows(
    monkeypatch: pytest.MonkeyPatch, value: str, limit: int | None
) -> None:
    monkeypatch.setenv("QUACKD_LOG_THINKING", value)
    assert thinking_limit_default() == limit
    assert parse_thinking_limit(value) == limit  # a flag and the environment agree


@pytest.mark.parametrize(
    ("value", "shown"),
    [(None, True), ("", True), ("1", True), ("0", False), ("off", False), ("No", False)],
)
def test_the_prompt_env_switch(
    monkeypatch: pytest.MonkeyPatch, value: str | None, shown: bool
) -> None:
    if value is None:
        monkeypatch.delenv("QUACKD_LOG_PROMPT", raising=False)
    else:
        monkeypatch.setenv("QUACKD_LOG_PROMPT", value)
    assert prompt_shown_default() is shown


def test_none_means_unlimited_thinking_on_the_console_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`None` used to mean "read the environment" here and "unlimited" in `render_lines`."""
    monkeypatch.setenv("QUACKD_LOG_THINKING", "100")
    event = LogEvent("llm", 0.0, {"thinking": "x" * 5000, "tool_calls": [], "usage": {}})

    buffer = io.StringIO()
    console = Console(file=buffer, width=400, force_terminal=False, no_color=True)
    ConsoleLog(console, thinking_chars=None)(event)
    assert "transcript.jsonl" not in buffer.getvalue()

    buffer = io.StringIO()
    console = Console(file=buffer, width=400, force_terminal=False, no_color=True)
    ConsoleLog(console, thinking_chars=thinking_limit_default())(event)
    assert "+4900 chars in transcript.jsonl" in buffer.getvalue()


def test_a_prefixed_view_names_its_robot_on_every_line_including_continuations() -> None:
    out: list[str] = []
    view = LineLog(lambda text, _style: out.append(text), prefix="duck-1  ")
    view(LogEvent("note", 0.0, {"text": "first line\nsecond line"}))
    view(LogEvent("intent", 0.1, {"kind": "move", "params": {}, "ok": True}))
    view.flush()
    assert out
    printed = "\n".join(out).splitlines()
    assert printed, "the view printed nothing at all"
    assert all(line.startswith("duck-1  ") for line in printed), printed
    assert any("second line" in line for line in printed)


def test_the_coordinators_events_render_as_flock_lines_in_the_recorders_words() -> None:
    cases = [
        (
            LogEvent("auction", 0.0, {"first_bid": "duck-1", "dist": 0.42}),
            "auction first bid duck-1 0.42 m",
        ),
        (
            LogEvent("claim", 0.0, {"kicker": "duck-1", "dist": 0.62, "spotter": "r-2"}),
            "claim   duck-1 (0.62 m), spotter r-2",
        ),
        (LogEvent("miss", 0.0, {"duck": "duck-0"}), "miss    duck-0, re-searching"),
        (LogEvent("kick_done", 0.0, {"kicker": "duck-2"}), "kicked  by duck-2, the spotter"),
        (
            LogEvent("verdict", 0.0, {"verdict": "moved", "moved_m": 0.51, "spotter": "r-1"}),
            "verdict moved 0.51 m by r-1",
        ),
        (LogEvent("member_dead", 0.0, {"duck": "duck-2", "last_hb": 3.0}), "dead    duck-2"),
        (
            LogEvent("member_end", 0.0, {"status": "stopped", "steps": 7}),
            "end     stopped after 7",
        ),
    ]
    for event, needle in cases:
        ((text, _),) = render_lines(event)
        assert text.startswith(needle), (event.kind, text)
    # the GIF's caption and the console line are the same words, one upper and one lower
    word, detail = flock_caption("claim", {"kicker": "duck-1", "dist": 0.62}) or ("", "")
    assert word == "CLAIM" and detail.startswith("duck-1 (0.62 m)")
    assert flock_caption("verb_end", {}) is None


def test_a_verb_ended_by_another_layer_is_yellow_and_keeps_its_own_word() -> None:
    data = {"name": "search_scan", "outcome": "preempted", "summary": "role change to kicker"}
    ((text, style),) = render_lines(LogEvent("verb_end", 0.0, data))
    assert "PREEMPTED: role change to kicker" in text
    assert style == "yellow", "a routine handover must not read as the red that means a bug"
    ((_, style),) = render_lines(LogEvent("verb_end", 0.0, {**data, "outcome": "error"}))
    assert style == "red"


def test_a_human_denial_renders_red() -> None:
    """A denial is a person saying no, and the line has to look like the refusal it is."""
    denied = LogEvent("gate", 0.0, {"gate": "confirm", "outcome": "denied", "verb": "kick"})
    ((text, style),) = render_lines(denied)
    assert style == "red" and "denied" in text
    allowed = LogEvent("gate", 0.0, {"gate": "confirm", "outcome": "allowed", "verb": "kick"})
    ((_, style),) = render_lines(allowed)
    assert style != "red", "a person saying yes is not a refusal"


def test_the_reprompt_line_quotes_what_the_model_was_told() -> None:
    """A model that answered with no tool call gets told so and asked again. Reading the log
    afterwards, what it was told is the whole reason the next turn looks the way it does."""
    event = LogEvent(
        "enforce",
        0.0,
        {
            "issue": "no tool call",
            "action": "re-prompting once",
            "text": "You must call exactly one tool.",
        },
    )
    ((text, _),) = render_lines(event)
    assert "no tool call: re-prompting once" in text
    assert "(You must call exactly one tool.)" in text


def test_the_done_line_says_sim_seconds_on_a_simulator() -> None:
    """An MCP client reading `done ok in 0.2 s` after a twenty second approach would think
    the robot teleported. On a simulator the two clocks are different numbers, and both."""
    sim = LogEvent(
        "tool_result", 0.0, {"ok": True, "elapsed_s": 0.2, "transport_s": 20.0, "clock": "sim"}
    )
    ((text, _),) = render_lines(sim)
    assert "20.0 s sim, 0.2 s wall" in text
    hardware = LogEvent("tool_result", 0.0, {"ok": True, "elapsed_s": 0.2, "transport_s": 0.2})
    ((text, _),) = render_lines(hardware)
    assert "in 0.2 s" in text and "sim" not in text
