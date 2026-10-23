"""The loop narrates itself: one stream of events, and three views of it.

Everything quackd does on the model's behalf used to happen behind a terminal that printed a
header and an outcome, with the transcript on disk as the only record. This module is the
event stream that record was written from, opened up: the loop, the executor and a wrapper
around the transport emit `LogEvent`s, and sinks render them. The transcript is the record
and always gets every event. The CLI's console and the MCP server's tool results are views of
the same stream, on by default and off with `--no-log` or `QUACKD_LOG=0`. Over MCP the
model is the client, so its reasoning never reaches quackd; there the log shows what quackd
can see: the verb, the gates that fired, every intent sent, what came back, and how long it
took.

Lines are ASCII first. A redirected stderr on Windows turns an arrow glyph into a `?`, and this
is exactly the output people redirect. Colour, not glyphs, carries meaning, and every line is
printed as plain text: a model that thinks `[/think]` must not crash the renderer.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import os
import re
import time
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from rich.text import Text

from quackd.agent.providers.pricing import fmt_usd
from quackd.transport.base import Ack, Intent
from quackd.verbs.watch import EDGE_AFTER, ERRORS_AFTER, LOST_AFTER, STALE_AFTER


@dataclass(frozen=True)
class LogEvent:
    kind: str
    t: float
    """Seconds since the event_log was made. The transcript stamps its own clock and ignores it."""
    data: dict[str, Any]
    """The payload, verbatim: what the transcript writes after `t` and `kind`."""


Sink = Callable[[LogEvent], None]

_OFF = ("0", "false", "no", "off")


def log_enabled_default() -> bool:
    """On unless `QUACKD_LOG` says otherwise. An empty value is on, so `QUACKD_LOG=` in a
    shell or a `.env` file switches nothing off by accident."""
    return (os.environ.get("QUACKD_LOG") or "1").strip().lower() not in _OFF


def parse_thinking_limit(raw: str | None) -> int | None:
    """`all` for everything, `0` for none, a number of characters otherwise; anything else
    is the default. Shared with `quackd log`, so a flag and the environment agree."""
    text = (raw or "").strip().lower()
    if text == "all":
        return None
    try:
        return int(text) if text else 2000
    except ValueError:
        return 2000


def thinking_limit_default() -> int | None:
    """How much of the model's thinking the console shows per turn: `QUACKD_LOG_THINKING` in
    characters, `all` for everything, `0` for none. The transcript always has all of it."""
    return parse_thinking_limit(os.environ.get("QUACKD_LOG_THINKING"))


def prompt_shown_default() -> bool:
    """Whether the console prints the system prompt once at the start: `QUACKD_LOG_PROMPT`.
    It is forty to seventy lines, worth reading once and tiresome on the fiftieth run of an
    afternoon, and it is in the transcript either way."""
    return (os.environ.get("QUACKD_LOG_PROMPT") or "1").strip().lower() not in _OFF


class EventLog:
    """Fan-out. One `record` sink whose failure is the run's failure (the transcript), and any
    number of observers whose failure is their own: a console that cannot print must not end
    a run, so an observer's exception is swallowed and counted."""

    def __init__(self, record: Sink | None = None, observers: Iterable[Sink] = ()) -> None:
        self.record = record
        self.observers: list[Sink] = list(observers)
        self._t0 = time.monotonic()
        self.dropped = 0
        """Events an observer raised on and therefore never showed."""

    def add(self, sink: Sink) -> None:
        self.observers.append(sink)

    def remove(self, sink: Sink) -> None:
        if sink in self.observers:
            self.observers.remove(sink)

    def emit(self, kind: str, /, **data: Any) -> None:
        # positional-only: a payload is free to have a field of its own called `kind`
        event = LogEvent(kind, round(time.monotonic() - self._t0, 3), data)
        if self.record is not None:
            self.record(event)
        for sink in self.observers:
            try:
                sink(event)
            except Exception:
                self.dropped += 1


# ── capturing one call's events (the MCP server) ────────────────────────────────────────

_capture: contextvars.ContextVar[list[LogEvent] | None] = contextvars.ContextVar(
    "quackd_log_capture", default=None
)


def a_person_was_asked(asker: object) -> bool:
    """Whether a `prompt` row may be written for what this callable answered.

    `--yes`, a flock's standing answer and the MCP server all answer without asking anybody,
    and they carry no mark, so nothing is recorded for them. The marked ones are the CLI's,
    and a mark that is callable is asked *now* rather than trusted from import time: a run
    with no terminal under it reaches the same callable, `input()` reads a pipe as happily as
    a person, and `yes | quackd run` would otherwise leave a record saying somebody cleared a
    verb on a robot nobody was standing next to. A record that under-claims is recoverable
    and one that invents a human is not.
    """
    mark = getattr(asker, "asks_a_person", False)
    if callable(mark):
        return bool(mark())
    return bool(mark)


A_PERSON = "a person"
A_PIPE = "a pipe"
STANDING = "a standing answer"
"""What `who_answered` names beside `--yes` and a flock's standing answer, which each carry
their own name (`answers_as`): a person really asked, the CLI's own question answered with no
terminal under it, and any other callable that answers without asking anybody."""


def who_answered(asker: object) -> str:
    """What answered a question put to `asker`, in the words a record names it with.

    `a person` only where `a_person_was_asked` says one was, so the name is held to the same
    rule as a `prompt` row. Otherwise what said it without asking anybody: the name an asker
    carries as `answers_as` (`--yes`, a flock's standing answer), `a pipe` for the CLI's own
    question put where no person is at the terminal, since its mark is a check made at the
    time of asking and it said nobody was there, and `a standing answer` for anything else,
    which claims nobody. `assess.answered_by` is this, beside the `human` the gate reads."""
    if a_person_was_asked(asker):
        return A_PERSON
    named = getattr(asker, "answers_as", None)
    if isinstance(named, str) and named:
        return named
    if callable(getattr(asker, "asks_a_person", None)):
        return A_PIPE
    return STANDING


def capture_sink(event: LogEvent) -> None:
    """An observer that appends to whatever `capturing()` is open in this context. The MCP
    server runs every tool call as its own task, and asyncio copies the context into a task
    at creation, so two calls on one robot never see each other's events."""
    buffer = _capture.get()
    if buffer is not None:
        buffer.append(event)


def unless_capturing(sink: Sink) -> Sink:
    """An observer that steps aside while a `capturing()` block is open in this context.

    The MCP server logs a call's lines in one block when the call ends, so only events that
    belong to no call — the heartbeat's note and the stop it sends — go straight through."""

    def forward(event: LogEvent) -> None:
        if _capture.get() is None:
            sink(event)

    return forward


@contextlib.contextmanager
def capturing() -> Iterator[list[LogEvent]]:
    events: list[LogEvent] = []
    token = _capture.set(events)
    try:
        yield events
    finally:
        _capture.reset(token)


# ── counting one verb's intents ─────────────────────────────────────────────────────────

_tallies: contextvars.ContextVar[tuple[Counter[str], ...]] = contextvars.ContextVar(
    "quackd_intent_tallies", default=()
)


@contextlib.contextmanager
def counting() -> Iterator[Counter[str]]:
    """One tally for the verb in flight in this context, chained onto its parents' so a nested
    verb's intents count for the composite too.

    A context variable rather than a list on the executor: asyncio copies the context into
    each task at creation, so two MCP calls running at once on one executor never see each
    other's frame, and a verb that is cancelled unwinds its own frame instead of popping
    somebody else's. With a shared stack, a `quack` that overlapped a `move` reported the
    move's intents as its own and the move reported neither its resends nor its stop."""
    tally: Counter[str] = Counter()
    token = _tallies.set((*_tallies.get(), tally))
    try:
        yield tally
    finally:
        _tallies.reset(token)


# ── the transport as verbs see it ───────────────────────────────────────────────────────


class LoggedTransport:
    """A transport for verbs: every intent they send becomes an `intent` event.

    Everything else is delegated to the real transport, so a verb's
    `getattr(ctx.transport, "stop_error", None)` still reaches the adapter. The tallies are
    read from the context at send time, not captured here, so an intent counts for whichever
    verb is in flight in the sending task and for each of its parents: that is how
    `approach_and` reports the intents its `go_to` sent."""

    def __init__(self, inner: Any, event_log: EventLog) -> None:
        self._inner = inner
        self._event_log = event_log

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):  # never delegate our own privates (copy, pickle, half-init)
            raise AttributeError(name)
        return getattr(self._inner, name)

    def _count(self, kind: str) -> None:
        for tally in _tallies.get():
            tally[kind] += 1

    def _robot_t(self) -> dict[str, float]:
        """The robot's own clock, so a burst's span is in the seconds the robot lived rather
        than the wall seconds a free-running simulator crosses in a fraction of the time."""
        with contextlib.suppress(Exception):
            return {"robot_t": float(self._inner.now())}
        return {}

    async def send_intent(self, intent: Intent) -> Ack:
        try:
            ack = await self._inner.send_intent(intent)
        except Exception as e:
            self._count(intent.kind)
            self._event_log.emit(
                "intent",
                intent=intent.kind,
                params=intent.params,
                accepted=False,
                reason=f"{type(e).__name__}: {e}",
                **self._robot_t(),
            )
            raise
        self._count(intent.kind)
        self._event_log.emit(
            "intent",
            intent=intent.kind,
            params=intent.params,
            accepted=ack.accepted,
            reason=ack.reason,
            **self._robot_t(),
        )
        return ack

    async def stop(self) -> None:
        try:
            await self._inner.stop()
        except Exception as e:
            self._count("stop")
            self._event_log.emit(
                "intent",
                intent="stop",
                params={},
                accepted=False,
                reason=f"{type(e).__name__}: {e}",
                **self._robot_t(),
            )
            raise
        self._count("stop")
        self._event_log.emit(
            "intent", intent="stop", params={}, accepted=True, reason=None, **self._robot_t()
        )


# ── rendering ───────────────────────────────────────────────────────────────────────────

Line = tuple[str, str]
"""(text, style). Styles are Rich style names the console maps; other writers ignore them."""

_LABEL = 8
_PAD = " " * _LABEL

DEFAULT_DETECTOR = "color_blob"
"""The colour detector's name, which `run_start` does not spell out on the `run` line: it is
the detector every run used before there was a choice, so naming it would move every line."""


def _label(name: str) -> str:
    return f"{name:<{_LABEL}}"


def _indent(text: str) -> str:
    return ("\n" + _PAD).join(text.splitlines()) if text else ""


@dataclass(frozen=True)
class LogLine:
    """One line of the story, before anybody decides what it looks like.

    Plain readers paste `label` into an eight-column gutter and get exactly the strings the
    MCP tool result has always carried. A terminal reads `mark` instead and draws a glyph,
    which it could not do from a formatted string without parsing its own output.
    """

    label: str
    """The word in the gutter: `verb`, `gate`, `->`. Empty for a block of somebody's prose."""
    body: str
    """What the line says, carrying no padding of its own."""
    style: str
    """A colour word. Views map it; the ones that only want text ignore it."""
    multiline: bool = False
    """Indent every line after the first under the gutter. True only where the body is
    somebody else's prose: the system prompt, an observation, thinking, a note."""
    mark: str | None = None
    """What kind of moment this is, for a view that draws glyphs: `start` · `send` · `ok` ·
    `fail` · `warn` · `other` · `note` · `flock` · `end`. None means the line speaks for
    itself."""


def _flatten(line: LogLine) -> Line:
    """A `LogLine` as the gutter-padded string every plain reader expects."""
    return (_label(line.label) + (_indent(line.body) if line.multiline else line.body), line.style)


def fmt_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.3g}"
    if isinstance(value, str):
        return repr(value) if len(value) <= 60 else repr(value[:57] + "...")
    text = repr(value)
    return text if len(text) <= 120 else text[:117] + "..."


def fmt_params(params: Mapping[str, Any] | None, *, drop_none: bool = False) -> str:
    """`drop_none` is for intent bursts, where a twist's `vy=null` on every one of two
    hundred lines is noise. Everywhere else a parameter the model left unset is part of what
    it chose, and `--dry-run` promises to show every one of them."""
    if not params:
        return ""
    return ", ".join(
        f"{k}={fmt_value(v)}" for k, v in params.items() if not (drop_none and v is None)
    )


_CLOCK_GAP_S, _CLOCK_GAP_FRAC = 0.5, 0.2


def _seconds(d: Mapping[str, Any]) -> str:
    """`1.4 s`, or `20.0 s sim, 1.4 s wall` when a free-running simulator's clock and the
    wall clock disagree. Both thresholds have to be crossed, or every sub-second sim verb
    would print two numbers to say the same thing."""
    wall = float(d.get("elapsed_s", 0) or 0)
    robot, label = d.get("transport_s"), d.get("clock")
    if robot is None or not label:
        return f"{wall:.1f} s"
    gap = abs(float(robot) - wall)
    if gap <= _CLOCK_GAP_S or gap <= _CLOCK_GAP_FRAC * max(float(robot), wall):
        return f"{wall:.1f} s"
    return f"{float(robot):.1f} s {label}, {wall:.1f} s wall"


def fmt_duration(seconds: float) -> str:
    """How long a run took, in the units a person would say it in.

    `43.2 s` under a minute, `1m 43s` under an hour, `1h 02m` above that. A verb's own seconds
    stay with `_seconds` above, which is a different question at a different scale: this one is
    for a whole run, where three decimal places of a two hour session are noise."""
    seconds = max(float(seconds), 0.0)
    # rounded first, because `59.97` printed to one place is `60.0 s`, which is a minute
    # spelled as if it were not one
    if round(seconds, 1) < 60:
        return f"{seconds:.1f} s"
    minutes, rest = divmod(round(seconds), 60)
    if minutes < 60:
        return f"{minutes}m {rest:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def _rate(value: Any) -> str:
    """A per-million-token rate as a rate card writes it: `$10`, `$0.042`, `$0.0375`.

    Not `fmt_usd`, which formats an amount somebody is charged and pads a dollar figure to two
    places. `$10.00/M in` reads as a bill for ten dollars; `$10/M in` reads as a rate."""
    if value is None:
        return "?"
    return f"${float(value):g}"


def _price_line(
    price: Mapping[str, Any] | None,
    decision_price: Mapping[str, Any] | None = None,
    *,
    watch: bool = False,
) -> str:
    """What the run was costed at, for the head of a replay.

    A sentence rather than a missing row when there is no rate, because "quackd has no price
    for this model" is exactly what a reader of a run with no cost figure needs to be told, and
    an absent line tells them nothing at all. `watch` is whether the run offered `watch`, which
    asks the decision LLM too, so its rate is then the decision LLM's and not the stepper's."""
    if not price:
        text = "unpriced: quackd has no rate for this model, so no cost was computed"
    else:
        source = str(price.get("source") or "")
        when = f", checked {price['checked']}" if price.get("checked") else ""
        where = f" ({source}{when})" if source else ""
        text = f"{_rate(price.get('input'))}/M in, {_rate(price.get('output'))}/M out{where}"
    if decision_price:
        payer = "decision LLM" if watch else "stepper"
        text += f", {payer} {_rate(decision_price.get('input'))}/M in"
    return text


def policy_row(policy: Mapping[str, Any]) -> str:
    """The policy server a run's arm hands its segments to, and the checkpoint it serves, for
    the `policy` row of a run's header: the live one `quackd run` prints and the one a replay
    opens with. The address is the record's, which was redacted before it was ever written."""
    served = policy.get("policy")
    return f"{policy.get('server')}  {served}" if served else str(policy.get("server"))


def play_row(on: bool, source: str, *, file_play: bool) -> str | None:
    """Whether a run's pilot may play a part, and whose word that was, for the `play` row of a
    run's header: the live one `quackd run` prints and the one a replay opens with. None for a
    run that neither plays nor turned a task file's play off, whose header is what it was."""
    if on:
        return "on, from the task file" if source == "task file" else f"on, from {source}"
    return f"off, from {source}" if file_play else None


def _listed(names: Any) -> str:
    """Names as a sentence lists them, "a", "a and b", "a, b and c", the way the loop lists the
    joints it names to the person holding the arm, or nothing where there is no list to read. The
    stepper's line and its shadow line both name the questions a decision LLM declined this way,
    so a reader meets one list the same way twice."""
    items = [str(name) for name in names] if isinstance(names, list) else []
    return ", ".join(items[:-1]) + " and " + items[-1] if len(items) > 1 else "".join(items)


def _ok(outcome: str) -> bool:
    return outcome == "ok"


_BAD = dict.fromkeys(("fail", "refused", "denied", "budget", "aborted", "error"), "red")
"""Red is for a run that did not do what was asked. A word from another layer (a flock's
`preempted`) is yellow: the verb ended early on purpose and nothing is wrong."""


_FLOCK_STYLE = {
    "auction": "cyan",
    "claim": "bold",
    "miss": "red",
    "kick_done": "bold",
    "verdict": "bold",
    "separation": "yellow",
    "auction_void": "yellow",
    "auction_waiting": "yellow",
    "member_dead": "red",
    "member_excluded": "red",
    "wedges_rotated": "cyan",
    "bid_rejected": "yellow",
    "talk": "cyan",
}


def flock_caption(kind: str, d: Mapping[str, Any]) -> tuple[str, str] | None:
    """A coordinator's decision as (WORD, detail), or None for anything that is not one.

    One vocabulary for two surfaces: the GIF caption is `WORD detail` and the terminal line
    is the word, lower-cased, in the label column. They were separate strings in the CLI and
    would have drifted the first time either was touched."""
    if kind == "auction":
        return "AUCTION", f"first bid {d.get('first_bid')} {float(d.get('dist') or 0):.2f} m"
    if kind == "claim":
        spotter = f", spotter {d['spotter']}" if d.get("spotter") else ""
        return "CLAIM", f"{d.get('kicker')} ({float(d.get('dist') or 0):.2f} m){spotter}"
    if kind == "miss":
        detail = f": {d['detail']}" if d.get("detail") else ""
        return "MISS", f"{d.get('duck')}{detail}, re-searching"
    if kind == "kick_done":
        return "KICKED", f"by {d.get('kicker')}, the spotter judges"
    if kind == "verdict":
        moved = f" {float(d['moved_m']):.2f} m" if d.get("moved_m") is not None else ""
        return "VERDICT", f"{d.get('verdict')}{moved} by {d.get('spotter')}"
    if kind == "separation":
        return "HOLD", f"{d.get('duck')} {float(d.get('dist') or 0):.2f} m away, ordered back"
    if kind == "auction_void":
        return "AUCTION", f"{d.get('auctions')} void: nobody eligible"
    if kind == "auction_waiting":
        return "AUCTION", f"waiting for a bid: {', '.join(d.get('missing_roles') or [])}"
    if kind == "member_dead":
        return "DEAD", f"{d.get('duck')}, last heartbeat at {float(d.get('last_hb') or 0):.1f} s"
    if kind == "member_excluded":
        return "OUT", f"{d.get('duck')} ({d.get('why')})"
    if kind == "wedges_rotated":
        return "SEARCH", f"round {d.get('round')}, wedges rotated {d.get('by_deg')} deg"
    if kind == "talk":
        # the one flock line a person actually reads for its content rather than its verdict,
        # so it carries the words and not a summary of them
        refused = "" if d.get("ok", True) else f" (refused: {d.get('summary')})"
        # a run with robots nearby and no flock has no member name to speak under
        who = d.get("src") or "the pilot"
        line = f"{who} -> {d.get('to')}: {d.get('text')}{refused}"
        # printed as text: a refusal may carry words a robot met chose
        from quackd.ui import printable

        return "TALK", printable(line)
    if kind == "bid_rejected":
        why = d.get("why") or f"missing {', '.join(d.get('missing') or [])}"
        return "BID", f"{d.get('src')} for {d.get('role')} rejected: {why}"
    return None


def render_events(
    event: LogEvent, *, thinking_chars: int | None = 2000, prompt: bool = True
) -> list[LogLine]:
    """One event as zero or more lines. The loop's own `verb` record, the `frame` record and
    `run_end` render nothing: the first duplicates `verb_end`, the second is a file on disk,
    and the CLI prints the outcome itself."""
    d = event.data
    k = event.kind
    if k == "run_start":
        adapter = d.get("adapter")
        robot = f"{adapter}:{d.get('transport')}" if adapter else str(d.get("transport"))
        head = f"{d.get('duck')} provider={d.get('provider')} model={d.get('model')} robot={robot}"
        # Only a detector other than the colour one, which every run before there was a choice
        # used and every run on a simulator still does: those lines stay exactly as they were,
        # in every transcript already written and in every MCP result a model reads.
        detector = d.get("detector")
        if isinstance(detector, str) and detector != DEFAULT_DETECTOR:
            head += f" detector={detector}"
        if d.get("dry_run"):
            head += " DRY RUN"
        # only a run that played, so every other line reads as it did, older records included
        if isinstance(play := d.get("play"), Mapping) and play.get("on"):
            head += " play"
        if "connect_s" in d:
            head += f" connected in {d['connect_s']:.2f} s"
        lines: list[LogLine] = [LogLine("run", head, "bold", mark="start")]
        lines.append(LogLine("tools", ", ".join(d.get("tools") or []), "dim"))
        memory = d.get("memory")
        if memory:
            lines.append(
                LogLine(
                    "memory",
                    f"{memory.get('notes')} notes, {memory.get('episodes')} earlier runs",
                    "dim",
                )
            )
        system = d.get("system_prompt")
        if prompt and system:
            n_lines = system.count("\n") + 1
            lines.append(
                LogLine(
                    "prompt",
                    f"system prompt, {len(system)} chars, {n_lines} lines "
                    "(also in transcript.jsonl as run_start):",
                    "dim",
                )
            )
            lines.append(LogLine("", system, "dim", multiline=True))
        return lines
    if k == "observation":
        if "error" in d:
            return [LogLine("obs", f"ERROR {d['error']}", "red", mark="fail")]
        return [LogLine("obs", str(d.get("text", "")), "", multiline=True)]
    if k == "llm_request":
        # transcripts recorded before the loop split the count carry `images` alone, and there
        # it meant exchanges, so read it as `with_image` and an old run replays line for line
        images = d.get("images", 0)
        with_image = d.get("with_image", images)
        seen = f"{with_image} with image"
        if images != with_image:  # more than one camera, so pictures outnumber the exchanges
            seen += f", {images} images"
        # only when there are any, so every transcript recorded before `--image` existed, and
        # every run made without it, replays line for line
        if task := d.get("task_pictures", 0):
            seen += f", {task} task picture{'s' if task != 1 else ''}"
        text = (
            f"step {d.get('step')}: {d.get('messages')} messages "
            f"({seen}) to {d.get('provider')} {d.get('model')}"
        )
        if d.get("reprompt"):
            text += " (re-prompt: it made no tool call)"
        # the same turn asked again after the API could not answer it: only from the second
        # try, so every transcript recorded before quackd asked again replays line for line
        if (attempt := d.get("attempt")) and attempt > 1:
            text += f" (try {attempt})"
        return [LogLine("llm>", text, "dim")]
    if k == "llm":
        if "error" in d and "retry_in_s" in d:
            # a failure quackd will ask again after: a warning, not the run's end, which the
            # red line below is
            return [
                LogLine(
                    "llm<",
                    f"ERROR {d['error']} after {d.get('latency_s', 0):.1f} s, asking again in "
                    f"{d['retry_in_s']:g} s",
                    "yellow",
                    mark="warn",
                )
            ]
        if "error" in d:
            return [
                LogLine(
                    "llm<",
                    f"ERROR {d['error']} after {d.get('latency_s', 0):.1f} s",
                    "red",
                    mark="fail",
                )
            ]
        out: list[LogLine] = []
        thinking = d.get("thinking")
        if thinking and thinking_chars != 0:
            text = str(thinking)
            if thinking_chars is not None and len(text) > thinking_chars:
                text = text[:thinking_chars] + (
                    f"... (+{len(text) - thinking_chars} chars in transcript.jsonl)"
                )
            out.append(LogLine("think", text, "dim italic", multiline=True))
        if d.get("text"):
            out.append(LogLine("llm<", str(d["text"]), "", multiline=True))
        calls = d.get("tool_calls") or []
        if not calls:
            out.append(LogLine("tool", "(no tool call)", "yellow", mark="warn"))
        for call in calls:
            out.append(
                LogLine("tool", f"{call.get('name')}({fmt_params(call.get('arguments'))})", "bold")
            )
        usage = d.get("usage") or {}
        total = d.get("usage_total") or {}
        tokens = f"in={usage.get('input_tokens', 0)} out={usage.get('output_tokens', 0)}"
        # Only when there was one, so a vendor with no cache, and every transcript recorded
        # before there were buckets, reads exactly as it always did. `in=` is the whole prompt
        # either way and `cached=` says how much of it came at the cheaper rate.
        if usage.get("cache_read_tokens"):
            tokens += f" cached={usage['cache_read_tokens']}"
        if usage.get("cache_write_tokens"):
            tokens += f" cache_write={usage['cache_write_tokens']}"
        if usage.get("reasoning_tokens"):
            tokens += f" reasoning={usage['reasoning_tokens']}"
        if total:
            tokens += (
                f" (run total in={total.get('input_tokens', 0)} out={total.get('output_tokens', 0)}"
            )
            # the running bill inside the running totals, because that parenthesis already
            # answers the question it belongs to: what has this run spent so far
            if d.get("cost_usd_total") is not None:
                tokens += f" {fmt_usd(d['cost_usd_total'])}"
            tokens += ")"
        if "latency_s" in d:
            tokens += f" latency={d['latency_s']:.1f} s"
        if d.get("cost_usd") is not None:
            tokens += f" cost={fmt_usd(d['cost_usd'])}"
        if d.get("stop_reason"):
            tokens += f" stop={d['stop_reason']}"
        # A refusal fallback re-ran the turn on another model. It is the one fact on this line
        # that changes what the rest of it means: the cost beside it was priced at the rate of
        # the model asked for, not of the one that answered.
        if d.get("served_by"):
            tokens += f" served_by={d['served_by']}"
        out.append(LogLine("tokens", tokens, "dim"))
        return out
    if k == "decision":
        # The stepper's own turn: what it chose and whether that was enough to act on. Drawn
        # whether or not it acted, because the turns it declined are the ones a reader most
        # wants to understand, and in shadow mode they are all of them.
        took = f"{float(d.get('latency_s') or 0):.2f} s"
        # A stepper question is a few hundred tokens and a fraction of a cent, and the whole
        # argument for one is the ratio between that and the model call it stands in for. Both
        # ride in the same parenthesis as the seconds, and a `~` marks a turn the decision LLM
        # did not count for itself, which quackd then estimated from the text it sent. Absent
        # on the turns that never reached the network at all.
        if (asked := d.get("usage")) is not None:
            mark = "~" if d.get("usage_estimated") else ""
            took += f", {mark}{asked.get('input_tokens', 0)} tok"
            if d.get("cost_usd") is not None:
                took += f" {mark}{fmt_usd(d['cost_usd'])}"
        # A camera frame went with this turn's request (`--decision-frames`). Said on a turn that
        # failed or was cut off as on one that was answered, because the frame left all the same,
        # and on no turn that sent none.
        if isinstance(d.get("frame"), Mapping):
            took += ", 1 frame"
        if d.get("gate") == "interrupted":
            # The run ended in the middle of this turn's request, by a second Ctrl-C or a flock's
            # cancel, which the record names where an error would be named. Read before the
            # error line, because nobody takes this turn and nothing failed: it is the request
            # the run was waiting on when it stopped, with what it had cost by then. Yellow with
            # a warning's mark rather than an error's red, as a declined question is.
            return [
                LogLine(
                    "decide",
                    f"interrupted by {d.get('error') or 'an interrupt'} after {took}",
                    "yellow",
                    mark="warn",
                )
            ]
        if d.get("error"):
            return [
                LogLine(
                    "decide",
                    f"ERROR {d['error']} after {took}, so the model takes this turn",
                    "red",
                    mark="fail",
                )
            ]
        gate = str(d.get("gate", ""))
        confidence = float(d.get("confidence") or 0.0)
        floor = float(d.get("floor") or 0.0)
        if gate == "taken":
            chosen = LogLine(
                "decide", f"{d.get('choice')} {confidence:.2f} >= {floor:.2f} ({took})", "bold"
            )
        elif gate == "below_floor":
            chosen = LogLine(
                "decide",
                f"{d.get('choice')} {confidence:.2f} < {floor:.2f}, to the model ({took})",
                "yellow",
                mark="warn",
            )
        elif gate == "refused":
            # A question the decision LLM declined rather than answered, by name. Yellow like a
            # missed floor and not dim like the gates below: nothing failed, but it is the
            # vendor's policy speaking about this state rather than its model answering, and
            # that is worth a reader's attention. A record without the names, which quackd never
            # writes, still says which gate it was.
            said = _listed(d.get("refused"))
            chosen = LogLine(
                "decide",
                f"refused to answer {said}, to the model ({took})"
                if said
                else f"refused, to the model ({took})",
                "yellow",
                mark="warn",
            )
        else:
            # `escalate`, `repeat`, `handover`, `shadow_only`, `unreadable`, `done`,
            # `need_human`, `not_offered`, `state_too_large`: nothing happened and the model
            # takes the turn, so this is dim like the request line it comes just before
            chosen = LogLine("decide", f"{gate}, to the model ({took})", "dim")
        stepper = [chosen]
        # The runners-up, because a 0.93 beside a 0.91 is a different decision from a 0.93
        # beside a 0.02, and the floor on its own cannot say which one you are reading.
        rest = sorted(
            (
                (float(value), str(label))
                for label, value in (d.get("probabilities") or {}).items()
                if label != d.get("choice")
            ),
            reverse=True,
        )[:3]
        if rest:
            stepper.append(
                LogLine("decide?", ", ".join(f"{label} {v:.2f}" for v, label in rest), "dim")
            )
        return stepper
    if k == "watch_tick":
        return [_watch_line(d)]
    if k == "decision_shadow":
        # Shadow mode's whole point in one line: what the stepper would have done beside what
        # the model did, on the same reading. The run is unchanged, so this is its only record.
        agrees = bool(d.get("agree"))
        picked = d.get("decision_choice")
        sure = d.get("decision_confidence")
        if picked is None:
            # The stepper chose nothing: a question declined, an answer with no label in it, a
            # call that failed, a turn with nothing on offer. Named by its gate, and by the
            # questions declined where there were any, because the line used to print the
            # missing choice and its missing confidence as `None 0.00`, which read as an answer
            # of its own.
            gate = str(d.get("decision_gate") or "no choice")
            said = _listed(d.get("refused"))
            stepper_said = f"refused to answer {said}" if gate == "refused" and said else gate
        elif isinstance(sure, int | float) and not isinstance(sure, bool):
            stepper_said = f"{picked} {float(sure):.2f}"
        else:
            # A label beside a confidence nobody could read, which is not a confidence of 0.00.
            stepper_said = str(picked)
        return [
            LogLine(
                "decide=",
                f"{stepper_said} "
                f"vs model {d.get('model_verb')}: {'agrees' if agrees else 'differs'} "
                f"({float(d.get('decision_latency_s') or 0):.2f} s against "
                f"{float(d.get('llm_latency_s') or 0):.1f} s)",
                "cyan" if agrees else "yellow",
                mark="note",
            )
        ]
    if k == "enforce":
        text = f"{d.get('issue')}: {d.get('action')}"
        if d.get("text"):  # the re-prompt's own words, which the record already carried
            text += f" ({d['text']})"
        return [LogLine("enforce", text, "yellow", mark="warn")]
    if k == "verb_start":
        text = f"{d.get('name')}({fmt_params(d.get('params'))})"
        if d.get("nested"):
            text = f"  {d.get('name')}({fmt_params(d.get('params'))}) [nested]"
        if d.get("source") and d.get("source") != "agent":
            text += f" from {d['source']}"
        return [LogLine("verb", text, "bold", mark="start")]
    if k == "gate":
        text = f"{d.get('gate')}: {d.get('outcome')}"
        if d.get("reason"):
            text += f" {d['reason']}"
        if d.get("params"):
            text += f" ({fmt_params(d['params'])})"
        if d.get("state"):
            text += f" [state: {d['state']}]"
        if d.get("last"):
            text += f" [last: {d['last']}]"
        refused = d.get("outcome") in ("refused", "denied", "exceeded", "fired")
        return [
            LogLine(
                "gate", text, "red" if refused else "yellow", mark="fail" if refused else "warn"
            )
        ]
    if k == "prompt":
        # What a PERSON was asked and what they said, which is only ever written down when
        # one was really there. The consequence is recorded separately by whoever acted on
        # it, so a "no" here is not a refusal in itself and is not drawn like one.
        said = "yes" if d.get("answer") else "no"
        asked = " ".join(str(d.get("question", "")).split())
        return [
            # `asked` and not `prompt`, which is already the label of the system prompt the run
            # opens with: the console draws a line called `prompt` as a section rule, and a
            # person's yes and no would both have come out as the same grey rule.
            LogLine(
                "asked",
                f"{d.get('what')}: {asked} -> {said}",
                "cyan" if d.get("answer") else "yellow",
                mark="note" if d.get("answer") else "warn",
            )
        ]
    if k == "intent":
        return [intent_log_line([event])]
    if k == "verb_end":
        outcome = str(d.get("outcome", "ok" if d.get("ok") else "fail"))
        verdict = "ok" if _ok(outcome) else ("FAIL" if outcome == "fail" else outcome.upper())
        n = sum((d.get("intents") or {}).values())
        tail = f" ({_seconds(d)}, {n} intent{'s' if n != 1 else ''})"
        text = f"{d.get('name')} {verdict}: {d.get('summary')}{tail}"
        if d.get("nested"):
            text = f"  {d.get('name')} {verdict}: {d.get('summary')}{tail}"
        good = _ok(outcome)
        return [
            LogLine(
                "<-",
                text,
                "green" if good else _BAD.get(outcome, "yellow"),
                mark="ok" if good else ("fail" if outcome in _BAD else "other"),
            )
        ]
    if k == "declare":
        won = d.get("outcome") == "success"
        return [
            LogLine(
                "declare",
                f"{d.get('outcome')}: {d.get('reason')}",
                "bold green" if won else "bold red",
                mark="ok" if won else "fail",
            )
        ]
    if k == "assess":
        word = d.get("verdict")
        if not word:
            return [LogLine("assess", f"invalid: {d.get('summary')}", "red", mark="fail")]
        text = f"{word}: {d.get('reason')}"
        if d.get("raised"):
            # the prompt raised, on EOF or click's `Abort`: the gate read that as no, and
            # nobody said it
            text += f" (the question went unanswered: the prompt raised {d['raised']})"
        elif d.get("human"):
            # who said it as the record names it (`who_answered`), and never a person the
            # record does not name: a run from before `answered_by` says only what was answered
            said = "go" if d["human"] == "go" else "no"
            who = d.get("answered_by")
            text += (
                f" ({who} said {said})"
                if who
                else f" (answered {said}, and the record does not say by whom)"
            )
        if d.get("ends_run"):
            text += " [the run ends before any motion]"
        style, mark = {
            "feasible": ("bold green", "ok"),
            "uncertain": ("yellow", "warn"),
        }.get(str(word), ("bold yellow", "warn"))
        lines = [LogLine("assess", text, style, mark=mark)]
        if d.get("estimates"):
            guessed = "; ".join(
                f"{e['object']} {e['quantity']}={e['value']:g} ({e['basis']}, {e['confidence']})"
                for e in d["estimates"]
            )
            lines.append(LogLine("est", guessed, "dim"))
        if d.get("needs"):
            lines.append(LogLine("needs", fmt_params(d["needs"]), "dim"))
        return lines
    if k == "memory":
        return [LogLine("memory", str(d.get("summary")), "cyan", mark="note")]
    if k == "hand_off":
        stage = str(d.get("stage", ""))
        reason = str(d.get("reason", ""))
        joints = d.get("joints") or {}
        refused = d.get("how") == "refused"
        # A refused release or hold is recorded under the stage it was trying for, and is said
        # as a refusal. "held:" over a take-hold that left torque off told the person holding
        # the arm, in the one word they read first, that something now holds it.
        label = {"released": "release refused", "held": "hold refused"}.get(stage, stage)
        said = label if refused else stage
        text = f"{said}: {reason}" if reason else said
        # Not after a hold refused over joints outside their travel (`outside`), whose reason
        # carries the readings that matter, each printed so it is never inside the travel the
        # same sentence gives (`said_past`). Whole degrees beside it named a joint a hair past
        # its ceiling at the ceiling itself, inside the travel the line said it was outside,
        # and a tenth would do the same a hundredth past an edge. Only there: a refusal whose
        # reason names no readings, an arm that moved as torque came on, which says "it is
        # holding where it is now" and names one joint, keeps the list that says where that
        # is. It was left out of every refused hold for a while, and a slip's line then said
        # the arm held "where it is now" and nowhere said where. The joints stay in the
        # record's event either way.
        if joints and not d.get("outside"):
            text += " (" + ", ".join(f"{j} {float(v):.0f}" for j, v in sorted(joints.items())) + ")"
        colour = "yellow" if refused or stage in ("released", "skipped") else "cyan"
        return [LogLine("hand", text, colour, mark="note")]
    if k == "release":
        # the end-of-run offer to a person holding an arm that missed its rest pose: said in
        # yellow either way, because both endings leave somebody something to do with the arm
        stage = str(d.get("stage", ""))
        reason = str(d.get("reason", ""))
        text = f"{stage}: {reason}" if reason else stage
        if d.get("how") == "refused":
            text += " (the arm refused)"
        return [LogLine("release", text, "yellow", mark="note")]
    if k == "note":
        return [LogLine("note", str(d.get("text", "")), "dim", multiline=True, mark="note")]
    if (caption := flock_caption(k, d)) is not None:
        word, detail = caption
        return [LogLine(word.lower(), detail, _FLOCK_STYLE.get(k, "cyan"), mark="flock")]
    if k == "member_end":
        return [
            LogLine("end", f"{d.get('status')} after {d.get('steps')} steps", "bold", mark="end")
        ]
    if k == "tool_call":
        args = {key: value for key, value in d.items() if key not in ("tool", "robot")}
        return [LogLine("tool", f"{d.get('tool')} {fmt_params(args)} on {d.get('robot')}", "bold")]
    if k == "tool_result":
        text = f"{'ok' if d.get('ok') else 'FAIL'} in {_seconds(d)}"
        if d.get("budget"):
            text += f" budget: {d['budget']}"
        return [LogLine("done", text, "dim", mark="ok" if d.get("ok") else "fail")]
    if k == "nearby":
        return [LogLine("nearby", _nearby_text(d), "cyan", mark="note")]
    return []


def _nearby_text(d: Mapping[str, Any]) -> str:
    """A robot met over Robot Passport, for the person watching a run: the name it gave itself,
    its tier, the name the pilot knows it by, and what it said. The name and the words are text
    a stranger wrote, so both are printed as text, escapes and all (`ui.printable`)."""
    from quackd.ui import printable

    robot = printable(str(d.get("calls_itself") or d.get("robot")))
    what = {
        "met": "met",
        "said": "said",
        "left": "said bye",
        "quiet": "went quiet",
        "asked": "asked for a courtesy",
        "share": "shared what it saw",
        "answer": "answered",
    }
    event = str(d.get("event"))
    text = f"{robot} ({d.get('tier')}, {d.get('robot')} to the pilot) {what.get(event, event)}"
    if d.get("text"):
        text += f": {printable(str(d['text']))}"
    if isinstance(details := d.get("details"), Mapping):
        shown = ", ".join(
            f"{k} {json.dumps(v) if isinstance(v, (dict, list)) else v}" for k, v in details.items()
        )
        text += f": {printable(shown)}"
    return text


def _watch_line(d: Mapping[str, Any]) -> LogLine:
    """One look of `watch`'s, from its `watch_tick` row: what it was answered, the move that made,
    and what the look cost, the way the stepper's `decide` line says a turn.

    Bold for a look that saw the thing, dim for one that held still, yellow for a camera that gave
    no fresh frame, red for a look with no answer to use or one declined, and the way the call
    ended on the look that ended it. A look sends at most one frame, which the time and the bill
    stand for, as `1 frame` does on the stepper's line."""
    n = d.get("n")
    gate = str(d.get("gate") or "")
    where, move, streak = (_as_mapping(d.get(key)) for key in ("where", "move", "streak"))
    took = ""
    if "latency_s" in d:
        took = f"{float(d.get('latency_s') or 0):.2f} s"
        if isinstance(asked := d.get("usage"), Mapping):
            mark = "~" if d.get("usage_estimated") else ""
            took += f", {mark}{asked.get('input_tokens', 0)} tok"
            if d.get("cost_usd") is not None:
                took += f" {mark}{fmt_usd(d['cost_usd'])}"
    cost = f" ({took})" if took else ""
    style, mark_as = "dim", None
    if gate == "seen":
        at = where.get("at")
        said = f"#{n} {where.get('choice')}" + (
            f" at {float(at):+.2f}" if isinstance(at, int | float) else ""
        )
        if move.get("moved"):
            said += (
                f" -> yaw {float(move.get('yaw_deg') or 0):+.0f}"
                f" pitch {float(move.get('pitch_deg') or 0):+.0f}"
            )
        else:
            said += ", holding"
        if move.get("edge") or move.get("clamped"):
            said += f", at the head's reach ({streak.get('edge')} of {EDGE_AFTER})"
        text, style = said + cost, "bold"
    elif gate in ("out_of_view", "unsure"):
        text = (
            f"#{n} {gate} {_spread_words(where)}, holding "
            f"({streak.get('lost')} of {LOST_AFTER}){cost}"
        )
    elif gate == "refused":
        text, style, mark_as = f"#{n} declined to say where{cost}", "red", "fail"
    elif gate in ("error", "unreadable"):
        why = d.get("error") or "an answer watch could not read"
        text = f"#{n} ERROR {why}{cost} ({streak.get('errors')} of {ERRORS_AFTER})"
        style, mark_as = "red", "fail"
    elif gate == "stale":
        text = f"#{n} no fresh frame: {d.get('error')} ({streak.get('stale')} of {STALE_AFTER})"
        style, mark_as = "yellow", "warn"
    else:
        text = f"#{n} {gate or 'no answer'}{cost}"
    if d.get("ended"):
        text += f"; ended: {d['ended']}"
        if mark_as is None:
            mark_as = "note"
    return LogLine("watch", text, style, mark=mark_as)


def _as_mapping(value: Any) -> Mapping[str, Any]:
    """`value` where it is a block, or an empty one, so a hand-edited row draws a line short."""
    return value if isinstance(value, Mapping) else {}


def _spread_words(where: Mapping[str, Any]) -> str:
    """The two likeliest answers of an unsure or out-of-view look, `left 0.41, middle 0.38`."""
    spread = where.get("probabilities")
    if not isinstance(spread, Mapping) or not spread:
        return ""
    top = sorted(
        ((float(p), str(label)) for label, p in spread.items() if isinstance(p, int | float)),
        reverse=True,
    )[:2]
    return "(" + ", ".join(f"{label} {p:.2f}" for p, label in top) + ")"


def render_lines(
    event: LogEvent, *, thinking_chars: int | None = 2000, prompt: bool = True
) -> list[Line]:
    """The same lines, padded into the gutter: what every plain reader has always seen, and
    what the MCP tool result carries (`tests/golden/log_lines.json` holds it to that)."""
    return [
        _flatten(line)
        for line in render_events(event, thinking_chars=thinking_chars, prompt=prompt)
    ]


def _ranges(events: list[LogEvent]) -> str:
    """`vx 0.05..0.2, vy 0, wz -1..0.4` for numbers; the distinct values for anything else."""
    seen: dict[str, list[Any]] = {}
    for event in events:
        for key, value in (event.data.get("params") or {}).items():
            if value is not None:
                seen.setdefault(key, []).append(value)
    parts: list[str] = []
    for key, values in seen.items():
        if all(isinstance(v, int | float) and not isinstance(v, bool) for v in values):
            lo, hi = min(values), max(values)
            parts.append(
                f"{key} {fmt_value(lo)}" if lo == hi else f"{key} {fmt_value(lo)}..{fmt_value(hi)}"
            )
        else:
            # only ever three are shown, so stop at four: this runs inside the console
            # observer, on the event loop, between two deadman resends
            distinct: list[str] = []
            for v in values:
                shown = fmt_value(v)
                if shown not in distinct:
                    distinct.append(shown)
                if len(distinct) > 3:
                    break
            parts.append(f"{key} {'/'.join(distinct[:3])}{'...' if len(distinct) > 3 else ''}")
    return ", ".join(parts)


def intent_log_line(events: list[LogEvent]) -> LogLine:
    """One line for a burst of intents of one kind: the intent itself when there is one, a
    count with the parameter ranges when a steering loop sent dozens."""
    first = events[0]
    kind = first.data.get("intent")
    if len(events) == 1:
        params = fmt_params(first.data.get("params"), drop_none=True)
        text = f"{kind}({params})" if params else f"{kind}"
        if not first.data.get("accepted", True):
            reason = first.data.get("reason") or "no reason given"
            return LogLine("->", f"{text} REFUSED: {reason}", "red", mark="fail")
        return LogLine("->", text, "dim", mark="send")
    if (first_t := first.data.get("robot_t")) is not None and (
        last_t := events[-1].data.get("robot_t")
    ) is not None:
        span = float(last_t) - float(first_t)  # the seconds the robot lived, not the wall's
    else:
        span = events[-1].t - first.t
    ranges = _ranges(events)
    text = f"{kind} x{len(events)} over {span:.1f} s"
    if ranges:
        text += f" ({ranges})"
    return LogLine("->", text, "dim", mark="send")


def intent_line(events: list[LogEvent]) -> Line:
    """The burst line, padded into the gutter."""
    return _flatten(intent_log_line(events))


def fan_out(*sinks: Sink | None) -> Sink:
    """One sink feeding several, ignoring the Nones.

    `AgentLoop` takes a single observer and a run wants two: the view that narrates and the
    status line that says what it is waiting for. A sink that raises must not starve the
    others, so every one is called and the first failure is re-raised afterwards, which
    leaves the `EventLog` counting exactly one drop for the event."""
    live = [sink for sink in sinks if sink is not None]

    def forward(event: LogEvent) -> None:
        failure: Exception | None = None
        for sink in live:
            try:
                sink(event)
            except Exception as e:
                if failure is None:
                    failure = e
        if failure is not None:
            raise failure

    return forward


PROGRESS_S = 2.0
"""How long a burst may build before the console shows it. A twenty second `go_to` on
hardware becomes one line every two seconds rather than twenty seconds of silence."""

MAX_BURST = 200
"""And how many intents may build, whatever the clock says. Twenty seconds at 10 Hz, which
is the bound that matters in a free-running simulator: it crosses that in under two wall
seconds, so the time rule never fires there."""


class LineLog:
    """A sink that renders events as lines through `write(text, style)`, coalescing a burst of
    one intent kind into one line. A `go_to` sends a different twist every 100 ms, so the
    burst is collapsed by kind, not by identical parameters, and flushed when anything else
    arrives, when the kind changes, or when it has been building for `progress_s`. Refused
    intents are never coalesced: each one is worth a line.

    That periodic flush is what makes the live view live. The only events during a twenty
    second approach are its own `move` intents, so without it the terminal showed the verb
    starting and then nothing at all until it ended. The wall clock is the right one to
    measure it by, even where the burst's own span is the robot's: a person waiting at a
    terminal waits in wall seconds."""

    def __init__(
        self,
        write: Callable[[str, str], None],
        *,
        thinking_chars: int | None = 2000,
        prompt: bool = True,
        progress_s: float | None = PROGRESS_S,
        max_burst: int = MAX_BURST,
        prefix: str = "",
    ) -> None:
        self._write = write
        self.thinking_chars = thinking_chars
        self.prompt = prompt
        self.progress_s = progress_s
        self.max_burst = max_burst
        self.prefix = prefix
        """Put before every line, continuation lines included: a flock's terminal interleaves
        its members, and each line has to say whose it is."""
        self._pending: list[LogEvent] = []

    def _out(self, text: str, style: str) -> None:
        if self.prefix:
            text = self.prefix + text.replace("\n", "\n" + self.prefix)
        self._write(text, style)

    def _show(self, line: LogLine, event: LogEvent | None) -> None:
        """One line, as this view draws it. The default is the gutter every plain reader
        expects; a terminal overrides this to draw glyphs and colour instead. `event` is the
        one the line came from, or None for a coalesced burst, which belongs to several."""
        self._out(*_flatten(line))

    def __call__(self, event: LogEvent) -> None:
        if event.kind == "intent" and event.data.get("accepted", True):
            if self._pending and self._pending[0].data.get("intent") != event.data.get("intent"):
                self.flush()
            self._pending.append(event)
            span = event.t - self._pending[0].t
            too_long = self.progress_s is not None and span >= self.progress_s
            if too_long or len(self._pending) >= self.max_burst:
                self.flush()
            return
        self.flush()
        for line in render_events(event, thinking_chars=self.thinking_chars, prompt=self.prompt):
            self._show(line, event)

    def flush(self) -> None:
        if not self._pending:
            return
        # write first, clear after: a write that fails (the EventLog swallows and counts it)
        # should leave the burst for the next flush rather than losing it
        self._show(intent_log_line(self._pending), None)
        self._pending = []


_BRACKETED = re.compile(r"^\[(.+)\]$")
_SEP = " · "


def _one_step(budget: str) -> str:
    """`step 3/40 · step 3/40, llm calls 3/40, 0.1/5 min` said the step twice, because the
    observation header and the budget line it embeds both began with it. The loop says it once
    now (`prompts.build_observation_text`), and this still reads a transcript from before."""
    head, sep, rest = budget.partition(_SEP)
    return rest if sep and rest.startswith(head) else budget


_GUTTER = 3
"""A glyph and a space in front of the label column. Two cells for the glyph, because the
ASCII half spells an arrow `->`; every other glyph in both halves is one cell wide."""


class ConsoleLog(LineLog):
    """The CLI's view: the same events, wearing what a terminal can wear.

    The plain renderer is a contract with a model (the MCP tool result carries it verbatim)
    and its arrows and its padded label column are frozen. A person reading a live run is
    not that reader. Here the arrow becomes a glyph in a gutter, the label column says a
    word instead, each step is ruled off, the system prompt is a block rather than forty
    lines of the same dim colour, and a failure is a shape as well as a red.

    Glyphs come from `ui.glyphs_for`, so the whole thing degrades to ASCII on the stream it
    is actually being written to. That is the part ADR-0029 was protecting when it said
    lines are ASCII first: a redirected stderr on Windows is cp1252, and this is exactly the
    output people redirect. It is still protected; it is just no longer paid for by every
    terminal that can do better. Nothing is printed as markup, because a model that thinks
    about `[/think]` must not raise a formatting error.
    """

    LABELS = {"->": "send", "<-": "result"}
    """The plain views keep the arrows, which is what the MCP result and every reader of
    `render_lines` has always seen. Here the arrow is the glyph, so the column says the
    word it stood for."""

    def __init__(
        self,
        console: Any,
        *,
        thinking_chars: int | None = 2000,
        prompt: bool = True,
        progress_s: float | None = PROGRESS_S,
        max_burst: int = MAX_BURST,
        prefix: str = "",
        prefix_style: str = "",
        header: bool = False,
    ) -> None:
        # `None` means unlimited here exactly as it does in `render_lines`: one sentinel, one
        # meaning. The environment is read by the caller, where `QUACKD_LOG` already is,
        # because that has to happen after `.env` is loaded rather than at import.
        super().__init__(
            self._print,
            thinking_chars=thinking_chars,
            prompt=prompt,
            progress_s=progress_s,
            max_burst=max_burst,
            prefix=prefix,
        )
        from quackd import ui

        self.console = console
        self.prefix_style = prefix_style
        self.header = header
        """Draw `run_start` as the panel a run opens with. True for `quackd log`, which has
        no other header, and False for a live run, where the CLI printed one before it
        connected and a second would say the same thing twice."""
        self._ui = ui
        self._glyphs = ui.glyphs_for(console)
        self._budget = ""
        """The bracketed step line lifted out of the last observation and drawn as a rule."""

    # ── drawing ─────────────────────────────────────────────────────────────────────

    def _print(self, text: str, style: str) -> None:
        """The plain line, for anything that reaches `LineLog`'s own path."""
        self.console.print(text, style=style or None, markup=False, highlight=False, soft_wrap=True)

    def _plain(self, text: str) -> str:
        """Spelled for the stream in hand. On a codepage that has no degree sign or arrow
        those characters are lost either way, so an ASCII stand-in is strictly better than
        the question mark they would otherwise arrive as."""
        return self._ui.degrade(text, self._glyphs)

    def _write_line(self, text: Any) -> None:
        # never markup: a model that thinks about `[/think]` must not raise a format error,
        # and soft_wrap so a long observation is never cropped
        self.console.print(text, markup=False, highlight=False, soft_wrap=True)

    def _rule(self, title: str = "") -> None:
        from rich.rule import Rule

        style = self._ui.STYLES["rule"]
        head = Text(self._plain(title), style=self._ui.STYLES["muted"]) if title else ""
        self._write_line(
            Rule(head, align="left", style=style, characters="-" if self._ascii else "─")
        )

    @property
    def _ascii(self) -> bool:
        return self._glyphs is self._ui.ASCII

    def _show(self, line: LogLine, event: LogEvent | None) -> None:
        """One line, as a terminal wears it: a glyph, a word, and the line itself."""
        if not self.prefix:
            # a flock prints three of everything, so the ruled-off forms are solo only
            if line.label == "prompt":
                self._rule(line.body)
                return
            if line.label == "" and line.multiline:
                self._block(line.body)
                return
        body = line.body
        if line.label == "obs" and self._budget and not self.prefix:
            body = body.split("\n", 1)[1] if "\n" in body else ""
        label = self.LABELS.get(line.label, line.label)
        glyph = self._glyphs.mark(line.mark)
        head = f"{glyph:<{_GUTTER - 1}} {label:<{_LABEL}}"
        for i, raw in enumerate(body.splitlines() or [""]):
            text = Text(overflow="fold")
            if self.prefix:
                text.append(self.prefix, style=self.prefix_style or None)
            if i == 0:
                text.append(f"{glyph:<{_GUTTER - 1}} ", style=line.style or None)
                text.append(f"{label:<{_LABEL}}", style=self._ui.STYLES["muted"])
            else:
                text.append(" " * len(head))
            text.append(self._plain(raw), style=line.style or None)
            self._write_line(text)

    def _block(self, body: str) -> None:
        """The system prompt: forty to seventy lines of somebody else's words, indented under
        the rule that introduced them and closed off so the run is visibly starting after."""
        pad_text = " " * _GUTTER
        for raw in body.splitlines():
            # respelled like every other line, and no padding on a blank one:
            # seventy lines of trailing whitespace is most of what a diff of a
            # redirected log turns out to be
            shown = self._plain(raw.rstrip())
            body_line = pad_text + shown if shown else ""
            self._write_line(Text(body_line, style=self._ui.STYLES["muted"]))
        self._rule()

    def _run_panel(self, event: LogEvent) -> None:
        d = event.data
        adapter = d.get("adapter")
        robot = f"{adapter}:{d.get('transport')}" if adapter else str(d.get("transport"))
        rows: list[tuple[str, Any]] = [
            ("provider", f"{d.get('provider')} ({d.get('model') or 'the first model it served'})"),
            ("robot", robot),
        ]
        if d.get("dry_run"):
            rows.append(("mode", Text("DRY RUN", style=self._ui.STYLES["warn"])))
        if memory := d.get("memory"):
            rows.append(
                ("memory", f"{memory.get('notes')} notes, {memory.get('episodes')} earlier runs")
            )
        if tools := d.get("tools"):
            rows.append(("tools", ", ".join(tools)))
        # the row the live header had, from what the connect heard, and only for a run whose
        # arm had a policy server, so every other replay opens as it always did
        if isinstance(policy := d.get("policy"), Mapping) and policy.get("server"):
            rows.append(("policy", policy_row(policy)))
        # the play row the live header had, from the record's own `play`, and none for a
        # record from before there was one
        if isinstance(play := d.get("play"), Mapping):
            contract = d.get("contract") or {}
            said = play_row(
                bool(play.get("on")),
                str(play.get("from")),
                file_play=bool(contract.get("play")),
            )
            if said is not None:
                rows.append(("play", said))
        # A replay has no CLI header in front of it and, until the record carried a wall clock,
        # no way at all of saying when the run it is showing you happened: the directory name
        # was the only answer, and a directory gets renamed and copied.
        if started := d.get("started_at"):
            rows.append(("started", str(started).replace("T", " ").replace("Z", " UTC")))
        if name := d.get("run_name"):
            rows.append(("run name", str(name)))
        # Only when the run recorded one either way. A `price` of None is a run quackd could
        # not cost and should say so; a record with no `price` KEY at all is a transcript from
        # before there were prices, and telling its reader it was "unpriced" would be
        # describing this release rather than their run.
        if "price" in d:
            rows.append(
                (
                    "price",
                    _price_line(d.get("price"), d.get("decision_price"), watch="watch" in d),
                )
            )
        hint = f"connected in {d['connect_s']:.2f} s" if "connect_s" in d else ""
        self._write_line(self._ui.run_header(str(d.get("duck")), rows, hint=hint))

    # ── reading ─────────────────────────────────────────────────────────────────────

    def __call__(self, event: LogEvent) -> None:
        if event.kind == "observation" and "error" not in event.data:
            # the loop puts `[step 3/40 · llm calls 3/40, 0.1/5 min]` at the top of every
            # observation. It is the one line that says where the run is up to, and it was
            # buried in the middle of a paragraph of state.
            first = str(event.data.get("text") or "").split("\n", 1)[0].strip()
            found = _BRACKETED.match(first)
            self._budget = _one_step(found.group(1)) if found else ""
            if self._budget and not self.prefix:
                self.flush()
                self._rule(self._budget)
        else:
            self._budget = ""
        if event.kind == "run_start" and not self.prefix:
            self.flush()
            if self.header:
                # a replay has no other header, so this is where the run introduces itself
                self._run_panel(event)
            elif "connect_s" in event.data:
                # a live run was introduced by the CLI before it connected; all this adds is
                # how long connecting took, which the panel could not have known
                self._show(
                    LogLine("run", f"connected in {event.data['connect_s']:.2f} s", "dim"), event
                )
            for line in render_events(
                event, thinking_chars=self.thinking_chars, prompt=self.prompt
            ):
                # the panel already carries the robot, the tools and the memory; what is
                # left for a line is the prompt, which is a block of its own
                if line.label == "run" or (self.header and line.label in ("tools", "memory")):
                    continue
                self._show(line, event)
            return
        super().__call__(event)


MCP_LOG_MAX_LINES = 30
_MCP_HEAD = 10


def cap_lines(lines: list[str], limit: int = MCP_LOG_MAX_LINES, head: int = _MCP_HEAD) -> list[str]:
    """The first few and the last many, with one line saying what was cut. A tool result is
    read by the model on every call; the uncapped log is on the server's stderr."""
    if len(lines) <= limit:
        return lines
    tail = limit - head - 1
    cut = len(lines) - head - tail
    return [
        *lines[:head],
        f"... {cut} more lines (the full log is on the server's stderr)",
        *lines[-tail:],
    ]


def call_lines(events: list[LogEvent]) -> list[str]:
    """One call's events as plain lines, uncapped.

    Guarded, unlike the `EventLog`'s observers: this runs outside the event_log, so a formatting
    error here would turn a robot's refusal into an MCP internal error rather than a result.
    """
    lines: list[str] = []
    # `progress_s=None`: this renders once the call has already ended, so splitting one
    # `-> move x200` into ten progressive lines would only spend the model's line cap
    view = LineLog(lambda text, _style: lines.append(text), prompt=False, progress_s=None)
    try:
        for event in events:
            view(event)
        view.flush()
    except Exception as e:
        lines.append(f"(the log could not be rendered: {type(e).__name__}: {e})")
    return lines


def render_call(events: list[LogEvent]) -> list[str]:
    """One MCP tool call's events as short plain lines for the `log` field of its result."""
    return cap_lines(call_lines(events))


__all__ = [
    "A_PERSON",
    "A_PIPE",
    "MAX_BURST",
    "MCP_LOG_MAX_LINES",
    "PROGRESS_S",
    "STANDING",
    "ConsoleLog",
    "EventLog",
    "LineLog",
    "LogEvent",
    "LogLine",
    "LoggedTransport",
    "Sink",
    "call_lines",
    "cap_lines",
    "capture_sink",
    "capturing",
    "counting",
    "fan_out",
    "flock_caption",
    "fmt_params",
    "intent_line",
    "intent_log_line",
    "log_enabled_default",
    "parse_thinking_limit",
    "prompt_shown_default",
    "render_call",
    "render_events",
    "render_lines",
    "thinking_limit_default",
    "unless_capturing",
    "who_answered",
]
