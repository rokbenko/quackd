# ADR-0029: The run narrates itself: one event stream, three views, on by default

**Status:** accepted, amended · **Date:** 2026-09-07 · Extends [ADR-0003](0003-three-loops.md) (the three loops are what the trace shows) and [ADR-0012](0012-safety-executor.md) (every gate the executor closes now says so) · Amends [ADR-0010](0010-providers.md) (the Anthropic request now carries a `thinking` parameter, so its reasoning has text to show) · Documented in [architecture.md](../concepts/architecture.md#trace)

**Amended 2026-09-21 by [ADR-0042](0042-the-log-is-the-whole-screen.md):** what this ADR calls the trace is now the log, because it holds every intent, every gate, three clocks, a cost per model call and the terminal session. `quackd/trace.py` is `quackd/log.py`, `TraceEvent` is `LogEvent`, the `Tracer` is an `EventLog`, `quackd trace` is `quackd log`, `--trace/--no-trace` and `--trace-prompt/--no-trace-prompt` are `--log/--no-log` and `--log-prompt/--no-log-prompt`, the three `QUACKD_TRACE*` variables are `QUACKD_LOG`, `QUACKD_LOG_THINKING` and `QUACKD_LOG_PROMPT`, `trace_dropped` in the summary is `log_dropped`, and the MCP result's `trace` key is `log`. Every CLI and environment spelling below still works for one release and says what it is called now in a yellow line when it is read; all of them go in 0.12. The MCP key was replaced outright, because a model reads it off the tool description rather than a changelog, and every reader still takes `trace_dropped`, so a run directory recorded before the rename replays unchanged. Nothing else in this ADR changes.

**Amended 2026-09-12 by [ADR-0033](0033-terminal-theme.md):** the "lines are ASCII first" decision below now applies where it was actually earned. The MCP tool result and every reader of `render_lines` still get exactly those bytes, frozen by `tests/golden/log_lines.json`. The terminal view renders the same events its own way, with glyphs, a rule per step and the system prompt as a block, and asks the stream it is writing to which half of the glyph table it can carry. A redirected stderr on Windows still gets `->` and `+`; a terminal that can draw an arrow gets one. Nothing else in this ADR changes.

**Amended 2026-09-07:** flock mode is traced too. Each member records into its own `ducks/<name>/transcript.jsonl` and gets a view prefixed with its name; the coordinator's decisions and the planner's one model call print under `flock`. The decision below that deferred it is superseded; nothing else in this ADR changes.

## Context

Ask quackd to walk in a circle and the terminal said three things: a header, an outcome, and
a run directory. Everything in between — which prompt the model got, what it reasoned, which
verb it picked, which rule refused it, what actually went down the wire to the robot, what
came back — existed only in `runs/<ts>/transcript.jsonl`, and only after the run had ended.

That was the wrong default for this project. quackd's whole claim is that a model's decisions
are *mediated*: a contract narrows them, an executor enforces them, a steering loop turns one
decision into a hundred intents. A person watching a robot move and unable to see any of that
has to take the claim on faith. Worse, the first real hardware run is the moment the
information matters most, and it was the moment it was least available.

Four things were missing rather than merely hidden:

- **The model's reasoning was never captured at all.** `ProviderTurn` had no field for it.
  Anthropic returns thinking blocks whose text is empty unless the request asks for a
  display, and quackd asked for nothing. Gemini's thought parts were being appended to the
  answer text, so they were replayed to the model next turn as things it had said.
- **The executor's decisions were invisible.** A refusal reached the model as a sentence and
  reached the human as nothing. Which of the seven gates fired, and on what state, was not
  recorded anywhere.
- **Intents were nowhere.** The transcript recorded verbs. `docs/safety.md` promised
  `--dry-run` "prints every intent a model would send", which was never true: the dry-run
  branch logged a verb name, and only under `--verbose`.
- **A run that failed said `loop exited unexpectedly`.** A bad key, a 429 or a dead camera
  is not a `SafetyStop`, so nothing caught it and `run_end` recorded a default string.

## Decision

- **One event stream, `quackd/trace.py`.** The loop, the executor, a wrapper around the
  transport and the MCP server emit `TraceEvent(kind, t, data)` into a `Tracer`. Nothing
  renders at the emit site.
- **The transcript is the record and is not optional.** It is the tracer's one *record* sink,
  whose failure is the run's failure. Views are *observers*, whose failure is their own: a
  terminal that cannot print must never stop a robot. `--no-trace` removes an observer, never
  the record. Existing transcript kinds keep their exact shape, so every reader still works;
  the new kinds are extra lines.
- **On by default, off with `--no-trace` or `QUACKD_TRACE=0`.** The default is read when the
  command runs rather than when the module is imported, so a `.env` line counts. This is the
  one call worth arguing about, and it goes to on: the person who most needs the trace is the
  one who does not yet know they need it.
- **The trace is the log.** With it on, the executor's own `→`/`←` lines would say every verb
  a second time, so the compact view stands down: `--verbose` is what you get with
  `--no-trace`, and on the MCP server the executor's log drops to DEBUG. Free-text lines are
  emitted as `note` events *in addition to* `log`, never instead of it, because `log` is a
  contract the flock's member records, the MCP logger and several tests rely on.
- **Over MCP the trace rides in the tool result.** MCP's logging capability is deprecated in
  the SDK quackd depends on and is a per-request opt-in, so it is not a channel. Every call
  that reaches an executor comes back with a `trace` list, capped at thirty lines, and the
  uncapped version goes to stderr where Claude Desktop already keeps it. The tools that never
  touch a robot get no trace: there is nothing behind the scenes to show.
- **A verb that starts always ends.** `run_verb` emits exactly one `verb_start` and, through a
  `finally`, exactly one `verb_end` carrying an outcome (`ok`, `fail`, `refused`, `denied`,
  `budget`, `aborted`, `error`). Seven exits used to leave nothing behind, including the two
  that matter most: a mid-verb abort and the repeat-failure abort.
- **Intents are traced at the verb boundary**, by wrapping the transport handed to verbs
  rather than the transport itself. A burst is coalesced by intent kind with its parameter
  ranges, because `go_to` recomputes its twist every 100 ms and identical-value coalescing
  would collapse nothing. A nested verb's intents count for its parent too, so a composite
  never reports zero.
- **The providers were taught to return reasoning.** `ProviderTurn.thinking`, filled from
  Anthropic's thinking blocks (the request now asks for `display: "summarized"`, without
  which the blocks come back empty), from `reasoning_content` or `reasoning` on an
  OpenAI-compatible server, from Gemini's thought parts, and from an inline `<think>` block a
  local server did not separate. Every one of these degrades: a model that rejects the
  parameter gets one retry without it and the run continues without the text.
- **Lines are ASCII first.** `->`, `<-`, `llm>`, `think`, `gate`. A redirected stderr on
  Windows renders an arrow glyph as `?`, and a redirected stderr is exactly what people do
  with a trace. Colour carries meaning instead. Every line prints as plain text, because a
  model that thinks about `[/think]` must not raise a markup error.

## Consequences

- **The terminal is not the record.** The console truncates thinking at 2000 characters
  (`QUACKD_TRACE_THINKING`) and the MCP result caps at thirty lines. The transcript has all
  of it, always. That split is deliberate and is what keeps the default affordable.
- **The transcript is bigger.** A twenty second `go_to` adds roughly two hundred `intent`
  lines. The file already carried one `frame` line per captured frame at 10 Hz, so this is
  the same order of magnitude, and `docs/assets/transcript-example.jsonl` predates the new
  kinds.
- **Anthropic runs now send a `thinking` parameter.** Display affects visibility only, not
  what is thought or billed, and raw chain of thought is never returned by any model.
- **Tests set `QUACKD_TRACE=0`.** The trace goes to stderr, and `CliRunner` folds stderr into
  `output`, so every CLI assertion would otherwise search a much larger haystack. One test
  each proves the default is on and that both switches turn it off.
- **Flock mode is unchanged.** It builds its own executors with no tracer, keeps its own
  `flock.jsonl` and its own `--verbose`. Tracing a many-robot run is future work.
  *Superseded in part by the amendment above: each member now records into its own
  transcript, every terminal line names its robot, and `flock.jsonl` also carries the
  planner's `llm_request` and `llm`, because that one model call belongs to no member.
  `--verbose` stands down under the trace here exactly as it does in a solo run, and
  `--no-trace` is what brings it back.*
- **The trace shows quackd's layer, not the wire.** An adapter's keepalive, a deadman resend
  inside a daemon and an adapter's own stop-on-close are its business and appear only in its
  logs.
