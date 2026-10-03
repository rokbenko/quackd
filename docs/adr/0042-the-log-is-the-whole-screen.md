# ADR-0042: The log is the whole screen

**Status:** accepted, amended · **Date:** 2026-09-21 · Renames what [ADR-0029](0029-tracing.md) called the trace and widens it to what the terminal showed · Extends [ADR-0041](0041-the-record-says-when-it-ran-and-what-it-cost.md) (the record carries the run's clocks and its money) · Retires the old spellings over one release the way [ADR-0017](0017-robot-adapters-and-manifest.md) retired `--transport` · Implemented in `quackd/log.py` (was `quackd/trace.py`), `quackd/ui.py`, `quackd/command.py`, `quackd/safety.py` and `quackd/cli.py` ([architecture.md](../concepts/architecture.md#log))

**Amended 2026-09-22:** the promise below was kept. 0.12 removes `quackd trace`, both
`--trace` flag pairs and the three `QUACKD_TRACE*` variables, along with the `sys.argv` scan in
`quackd/cli.py` and the environment fallback in `quackd/log.py` that existed only to carry
them. The subcommand and the flags are refused the way any unknown one is, and a refusal writes
no run directory. The variables do not simply go quiet, which is the failure the fallback
existed to prevent: a `QUACKD_TRACE*` name still set is ignored and named in one yellow line
before the header, the line 0.12 gives `QUACKD_MODEL`. Because that check moved to the app
callback, where the command line is read, rather than staying where each value was consulted,
a run now names every old line in a `.env` at once rather than only the one it would have
read, which lifts the limit the "Two spellings of six things" bullet under Consequences
describes. The reader still takes `trace_dropped` out of a `summary.json`, because a run
directory outlives the release that wrote it. Nothing else in this ADR changes.

**Amended 2026-09-29:** the new key that "One field is not held to that" below leaves to
whoever next opens the `assess` event is there. `answered_by` names what answered the pilot's
doubt, a person, `--yes`, a flock's standing answer, a pipe or any other standing answer, held
to the rule a `prompt` row is (`quackd.log.who_answered`), and `human` stays the gate's state.
The line drawn from the event names `answered_by` and no longer says `(the human said go)`. A
run recorded before the key existed is drawn as `(answered go, and the record does not say by
whom)`. A prompt that raised answered nothing, and the event carries `raised`, what it raised,
in place of `answered_by`, drawn as the question gone unanswered.

## Context

Two things were wrong here and only one of them is a name.

[ADR-0029](0029-tracing.md) called this a trace because in 0.7 that is what it was: the loop
narrating itself to a terminal that had been printing a header and an outcome. What it holds
now is a different object. Every intent sent to the robot goes through it, which is roughly two
hundred lines for one twenty second `go_to`, so the record carries the body's own movement.
Every gate the executor closed is in it, with the state it fired on. Since
[ADR-0041](0041-the-record-says-when-it-ran-and-what-it-cost.md) it carries three clocks and a
cost per model call, which is exactly what the sentence at the top of `README.md` about where
78.8 seconds of one hardware run went had to do without. That run is the SO-101 of 2026-09-15
under 0.9.0, where neither `wall_s` nor `llm_latency_s` existed, so the 62.1 seconds the model
took is a sum somebody did by hand down a column of ten per-call figures in the README's own
table. A run recorded today carries that number in `run_end` and in `summary.json`, which is the
argument for the clocks and for this: the number was always in the run and only the record was
missing. None of that is debugging output.
A trace is the thing you switch on once you already suspect something; this
is on by default, it is what a run is argued about from afterwards, and it is the evidence
behind this project's own claims. The word undersold it, and it did so exactly where the stakes
were highest, because `--no-trace` reads like *stop recording* and has never meant that.

The rename was asked for, and asked for in full: the CLI, the module, the classes, the
environment variables, the field on disk and the key on the wire. That those last two are
contracts was said before the call was made rather than discovered afterwards. The answer was
to rename them anyway and pay for it in the open, which is what most of the decision below is.

The second thing has nothing to do with the name. `transcript.jsonl` holds everything quackd
did and nothing of what a person saw while it did it, and those differ by more than colour. The
screen is where a run's questions are put: the confirm gate, the feasibility verdict, an
acknowledgement, and the two halves of a `--by-hand` handover. The record kept what each answer
caused (`gate.answer`, `assess.human`, the `hand_off` stages) and never the exchange itself, and
it could not have caught the answer anyway: the question is written to stderr and the answer is
echoed by the terminal, so no view of quackd's ever prints it and no pipe ever sees it. A run
also knew nothing about what it had been asked to do. Which robot, which model, which budget,
whether it was a dry run: half the story of any run is in the flags, and reading a directory a
month later meant guessing at them. Both gaps close the same way the wall clock's did in
ADR-0041, by writing the thing down once at the top.

## Decision

- **The record is the log, and the module is `quackd/log.py`.** `TraceEvent` is `LogEvent`,
  `TraceLine` is `LogLine`, `TracedTransport` is `LoggedTransport`, `ConsoleTrace` and
  `LineTrace` are `ConsoleLog` and `LineLog`, `trace_enabled_default`, `intent_trace_line` and
  `MCP_TRACE_MAX_LINES` follow their module, and `quackd trace` is `quackd log`. The rename
  changed no kind, no order and nothing about the transcript being the record rather than an
  observer. One kind is new in this release, `prompt`, and it is the feature work below rather
  than anything the rename did.

- **The name `log` was already taken, so the rename routed around it instead of through it.**
  `RunConfig.log` and `Executor.log` are the `--verbose` callback, and
  [ADR-0029](0029-tracing.md) says so in as many words: free text is emitted as a `note` event
  *in addition to* `log`, never instead of it, "because `log` is a contract the flock's member
  records, the MCP logger and several tests rely on". `member_log` is a record kind in
  `flock.jsonl` and is what a flock member's free text lands in. And inside `quackd/`, `log` was
  the name bound to a `logging.getLogger` in four places, three of them module globals. So the
  callback keeps it,
  the emitter became `EventLog` and the attribute on everything that holds one is `.event_log`,
  the live view became `RunConfig.view` because that is what it is, and the four logging
  bindings in `quackd/` became `logger`. Inside `quackd/` one word now means one thing in each
  layer, which is the whole return on the rename and is worth more than the churn it cost.
  `quackd/` is the honest scope and it is worth saying rather than implying: seven more
  `log = logging.getLogger` bindings sit in this workspace, four under `adapters/microduck` and
  three under `bridge/`, and this rename reached none of them.

- **Every reader takes both spellings of the dropped counter; every writer emits one.**
  `trace_dropped` became `log_dropped` where it is written, and the one place that reads it
  back, the `run_end` a replay parses in `quackd log`, takes either. A run directory recorded
  before this rename replays exactly as it did, which is the only promise that matters about a
  file somebody already has.

- **The old CLI and environment spellings live exactly one release.** `quackd trace` is a hidden
  alias of `quackd log`; `--trace/--no-trace` and `--trace-prompt/--no-trace-prompt` are second
  spellings on the same `typer.Option` as `--log/--no-log` and `--log-prompt/--no-log-prompt`;
  `QUACKD_TRACE`, `QUACKD_TRACE_THINKING` and `QUACKD_TRACE_PROMPT` are read when the
  `QUACKD_LOG*` name is unset. Each says so once per process and the command runs, subject to
  one limit on the environment names that has a bullet of its own below. The
  subcommand and the flags print a yellow line through `ui.err_console`, off `sys.argv` rather
  than off the parsed value, because Click hands both spellings to the same parameter and by
  then they are indistinguishable; the environment fallback prints through `ui.err_console` as
  well, inside a `try` that falls back to `sys.stderr`, because `quackd/log.py` is imported long
  before the consoles are configured and a warning that raised on its way out would take the run
  with it. All of it goes in 0.12. This is the shape
  [ADR-0017](0017-robot-adapters-and-manifest.md) set when `--robot` replaced `--transport`: one
  release of grace, one line per process, a date on the removal, and the removal happened.

- **An environment name warns only where it is read, and that is a limit somebody's `.env`
  depends on knowing.** `QUACKD_TRACE=0` beside `QUACKD_TRACE_PROMPT=0` warns about the first
  and says nothing about the second. Turning the log off means no `ConsoleLog` is built, so the
  prompt setting is never consulted, and a name nobody reads cannot announce that it was
  renamed. A flag does the same thing one level up: `--log` and `--no-log` answer the question
  `QUACKD_LOG` would have answered, so a run passing either never reads it and is never told
  that name moved. All three are named on a run that has the log on and passed no flag, and the
  same file is told about one of them on a run with the log off. Reading all three eagerly to warn about them would mean reading
  settings the run has no use for, which is a worse thing to own than a warning that arrives
  when the setting first matters. Somebody who switched the log off in a `.env` a year ago is
  therefore told about exactly one of their old names before 0.12 removes the others.

- **The MCP result key was replaced outright rather than doubled.** `robot_run_verb`,
  `robot_observe`, `robot_say` and `robot_assess_task` come back with `log` and nothing named
  `trace`. The reader there is a model, and it learns the key from the tool description on every
  session rather than from a changelog, so there is nobody to warn and no yellow line to print.
  A result carrying both spellings would hand it a second copy of up to thirty lines on every
  call that touched a robot, paid for in context each time. One key, described where a model
  actually reads. `robot_observe` is the one of the four that is not a dictionary at all,
  because it has pictures to hand over: it answers with content blocks, so its log arrives as a
  text block opening `log:` and a client looking there for a key of either spelling finds none.

- **The terminal is kept by a console that tees, `ui.TeeConsole`.** `make_console()` returns one,
  so everything quackd prints through `ui.console` or `ui.err_console` is also printed into the
  open capture. The forward happens *after* `super().print` rather than before, because
  `rich.rule.Rule` truncates its title `Text` in place at the width it is drawn at, and whichever
  console renders first decides what the other one gets: the terminal renders first, so the file
  is a copy of the screen and not the other way round. The capture writes into a plain `Console`
  with `color_system=None` and `no_color=True`, so `terminal.txt` carries no escape codes and no
  terminal hyperlinks and stays greppable.

- **That settles the escapes quackd would write, and the sink settles the ones it would carry.**
  Every C0 control except tab and newline, and DEL, is dropped on the way into the file. Rich
  strips a list of its own and ESC is not on it, so an escape that arrived inside *text* rather
  than from a style went straight through, and the text here is not ours: it is the goal
  somebody typed, the body of a `.duck` fetched off the internet, whatever the model said it was
  thinking and an error message from a robot. `ESC[2J` in any of those clears the screen of
  whoever reads the file with `cat`, `ESC]0;...BEL` retitles their window. Dropped rather than
  escaped, because this file is read by a person and not parsed, and nothing is lost by it:
  `transcript.jsonl` beside it keeps the same characters, escaped by JSON, which is both safe
  there and recoverable from there.

- **Three details in that tee are load bearing and none of them is obvious.** `console.line()` is
  not forwarded: its one caller on the run path is `Live.stop`, which prints a blank line and
  then erases it again with a cursor control that does not go through `print`, so following it
  would put a blank line in the file at every prompt, every paused status and every spinner. The
  capture's lock never spans `super().print`: `Live` holds its own lock around the refresh
  thread's print, and taking ours first inverts that order between two threads. And the sink's
  `encoding` is set to the printing console's on every forward, because Rich derives `ascii_only`
  from nothing but `file.encoding`, and a UTF-8 stdout beside a cp1252 stderr have to render in
  the file the way each of them rendered on the screen.

- **Nothing is written before the run directory exists, and a refused run leaves nothing.** The
  capture buffers into a `StringIO` from the first line, which is how the header and a bad flag's
  one sentence are in the file at all; `ui.attach_capture(run_dir)` opens the file, writes what
  was buffered into it and sends every later print straight there. The capture is opened around
  the whole body of `run` and `record`, so pre-flight refusals are inside it, and closing an
  unattached capture writes nothing. A failure to write disables the capture and is remembered on
  `.broken`, never raised: a file nobody asked for must not cost the terminal a line, still less
  stop a robot, which is [ADR-0029](0029-tracing.md)'s rule about observers applied to one more
  observer.

- **The command is recorded, with the secrets taken out of it, in `quackd/command.py`.**
  `run_start.command` and `summary.json` carry the argv as a list with `argv[0]` replaced by the
  literal `quackd`, so the record says what was run rather than which interpreter path ran it,
  and `version` goes beside it. `--api-key` and `--token` have their values replaced by `***` in
  both spellings a shell allows, `--api-key sk-…` and `--api-key=sk-…`. The flag stays, because
  *that a key was passed* is usually the thing a reader is checking. A trailing
  `--api-key` with nothing after it is left alone, and so is an `--api-key=` given nothing after
  the sign: there is no secret in either, and writing `***` where nothing was typed puts a secret
  in the record that never existed. The record should show the mistake as it was made. The one
  mistake that is not shown as made is a secret flag standing where a value belongs,
  `--api-key --token sk-...`, which a shell reads as a key of `--token` and a stray argument
  holding the real thing: there the tie goes to hiding, because a flag name is never worth
  hiding and a key always is. Redaction is by flag name and it happens in one module rather
  than at each call site, because a run directory is a thing people paste into issues and attach
  to bug reports, and a key that reaches one has to be rotated.

- **Two more ways a credential reaches the record, and both are taken out where they are
  written.** A URL is a second way to type a password, so `--base-url`, `--address` and
  `--camera-url` keep their scheme, host, port, path and every query parameter that is not
  credential-shaped, and lose two things: the password out of `user:pass@`, and any query
  parameter named like a credential. The username stays, because it is how somebody recognises
  which account was used and is not the half that has to be rotated.
  `https://rok:hunter2@gateway/v1?api_key=sk-live&model=x` is recorded as
  `https://rok:***@gateway/v1?api_key=***&model=x`. The host is the whole reason that flag is in
  the record and the credential is never the useful half. Anything that does not parse as a URL
  is left exactly as it was typed rather than guessed at, because a mangled value in the record
  sends a reader after the wrong bug. `--extra-body` is the other one, and it is
  the one no argv redaction could have reached, because the same object also arrives as
  `QUACKD_EXTRA_BODY` and a variable is not in `sys.argv` at all: it is walked on the way into
  `run_start` and every credential-named key replaced, at any depth, because an `authorization`
  header is precisely the kind of field a vendor asks to have passed through.

  The gap that leaves is worth naming here rather than leaving to be found. `--extra-body` is
  not a secret flag, so the JSON as typed is still in the recorded `command` and in
  `summary.json`. What is redacted is the field, not the command line, and somebody who puts a
  key in that object on the command line has put it in the record. The flag list is the seam to
  widen on the day that is how people pass one.

- **A `prompt` event is written only where a person was really asked.** The new kind is
  `{what, question, answer}` with `what` one of `confirm`, `decide`, `acknowledge` or `hand_off`.
  The emitter reads `asks_a_person` off the asker, and the CLI sets that mark on
  `_confirm_prompt`, `_decide_prompt`, `_acknowledge_prompt` and `_TerminalHandOff`. `allow_all`,
  `deny_all`, `_yes_to_go` and a flock's standing answers carry no mark, so a `--yes` run writes
  no `prompt` row. A record claiming a question was put to somebody who was not there is worse
  than no record, and worst precisely where it matters: the confirm gate is the one place the
  file would say a human authorised something.

- **The mark is a question asked at the moment of asking, not a fact settled at import.** A mark
  that is callable is called, and the one the CLI gives its three askers is `_a_person_is_there`,
  which looks `_can_prompt` up by name and calls it, so what is read is whether stdin is a
  terminal right now. The reason is `yes | quackd run` and `quackd run < answers.txt`: they reach
  those same three callables, `input()` reads a pipe as happily as a person, and a flat `True`
  would have left a record saying somebody cleared a verb on a robot nobody was standing next to.
  The gate still opens on a pipe, because that is what the pipe asked for and it is what quackd
  has always done. What is withheld is only the testimony: no `prompt` row, and the gate's own
  `reason` stopped saying `a human said yes` where nobody was asked, saying
  `the confirm gate was allowed` or `the confirm gate was denied` instead. `_TerminalHandOff`
  keeps a flat `asks_a_person = True` because it is only wired in where `_can_prompt()` has
  already said yes.

- **Both flock runners call back the moment their directory exists.** `run_pilot_flock` and
  `run_flock` take `on_run_dir: Callable[[Path], None] | None`, called before any member writes
  a file, and the CLI passes `ui.attach_capture`. A flock's screen is kept once at the run root,
  because the terminal was one terminal however many robots were narrating into it.

## Why not

- **Keep the name and only add the screen.** By far the cheapest option, and it leaves the one
  flag that decides whether a robot's own record is written reading as though it were about
  writing the record. Every argument for the rename is an argument against carrying `trace` on a
  file that now holds movement, timing, cost and the session itself, and the cost of the rename
  is bounded and dated: one release of two spellings and one yellow line.

- **Free the name by renaming the `--verbose` callback instead.** It looks like the smaller
  change and it is the larger one. `log` there is a contract by ADR-0029's own words, it is the
  sink behind `member_log` in `flock.jsonl`, and several tests read what it produced. Renaming
  the emitter cost an attribute nobody outside this repo names; renaming the callback would have
  cost a record kind on disk in exchange for nothing a reader ever sees.

- **`Console(record=True)` and `export_text()`.** The obvious answer, and it records segments
  after the render hooks have run, which means every spinner frame at four a second and every
  live-region redraw lands in the export. What comes out is not the screen, it is the screen's
  animation flattened, and it grows without bound on a long run.

- **A tee at the stream level, wrapping `sys.stdout` and `sys.stderr`.** It survives anything
  that prints, including a library that never heard of Rich, and it buys that by losing the
  interleaving: two wrappers cannot tell you which of two writes came first, and quackd puts its
  answers on one stream and its narration on the other on purpose. It also still never sees a
  typed answer, because the terminal echoes that and no process writes it.

- **Ask the user to capture it from outside, with `script` or `tee`.** It is the right tool and
  it is not a thing a CLI can require. The person who most needs the file is the one who did not
  know they would need it, which is the same argument ADR-0029 used to make the log on by
  default, and `tee` gets the escape codes with it while `script` gets the cursor motion too.

## Consequences

**An MCP client that reads `result["trace"]` now reads nothing.** There is one key, `log`, and
this is the only spelling in the rename with no transition. A client that indexes the key raises;
one that uses `.get` silently shows an empty log, which is the worse of the two, so it is said
here and in [mcp.md](../guides/mcp.md) rather than left to be noticed.

**Old run directories replay unchanged, and new ones replay on an old quackd with the new parts
missing.** `log_dropped` is what gets written now, `trace_dropped` is still read, and the replay
that parses a `run_end` is where that fallback lives. Nothing was taught to read forwards and
nothing had to be: 0.10 does not know `command`, `version`, `prompt` or `log_dropped`, and
`quackd trace` on 0.10 replays a record written here without complaining, printing the three
counters it has always had, skipping the `prompt` row it has no renderer for and taking the
dropped counter, which it looks for under the old name, as zero. That was run against v0.10.0
rather than reasoned about. Losing four fields is not the same thing as being unreadable, and
saying the stronger of the two would send somebody off to rewrite a record that replays as it is.

**Every run directory now contains the session, which is a new thing to be careful with.** The
command line in it is redacted by flag name: a key passed as `--api-key` or `--token` is `***`,
and a password or a credential-named query parameter inside a `--base-url`, an `--address` or a
`--camera-url` is too. Nothing else on the screen is, and the list of exceptions matters less
than the rule: a key in an environment variable that an error message echoed, a key typed inside
`--extra-body`, a model's reasoning about a private task, a path with somebody's name in it are
all on the screen and therefore in the file. `terminal.txt` is the screen rather than the
record, so `--no-log` takes the narration out of the file exactly as it takes it off the
terminal, and a run that prints outside quackd's two consoles, a bare `print` or a library
writing straight to a stream, is on the screen and not in the file.

**Two spellings of six things, until 0.12.** `quackd trace`, `--trace/--no-trace`,
`--trace-prompt/--no-trace-prompt` and the three `QUACKD_TRACE*` variables all work. The
subcommand and the flags warn whenever they are typed; a variable warns only on a run that reads
it, which is the limit above, so a `.env` with the log switched off hears about one of its three
old names and not the other two. The warning is one line per spelling per process on stderr, and
the subcommand's and the flags' are printed from the app callback, before a command body has run
and therefore before the capture exists, so `_terminal_header` replays them into `terminal.txt`
at the top of the file where they were on the screen. The removal is a dated promise and 0.12 has
to keep it, as 0.5 kept 0.4's.

**The prompt rows are only as honest as the mark.** A new asker that reaches a person and is not
given `asks_a_person` writes no row, and the run looks like one nobody was asked. That failure is
silent and it is the right direction for it to fail in: the record under-claims rather than
inventing a human. Anything added beside `_confirm_prompt`, `_decide_prompt`,
`_acknowledge_prompt` and `_TerminalHandOff` has to carry the mark, and a standing answer must
never be given one.

**One field is not held to that, and it is the one field that cannot simply be.** `assess.human`
is set to `go` whenever the decide callable answered yes, including `--yes`, a flock's standing
answer and a pipe on stdin, and the console draws it as `(the human said go)`. Blanking it where
nobody was asked is not available: `Verdict.go` is `feasible or (uncertain and human == "go")`,
so the field is what lets an uncertain verdict proceed at all, and emptying it would refuse every
`--yes` run the pilot was unsure about. The field is doing two jobs, the gate's state and a claim
about a person, and only the second is wrong. Separating them means a new key on the `assess`
event saying who cleared it, which is a wire change and belongs to whoever next has reason to
open that record rather than to this one. Until then the `prompt` rows are the honest half: where
one sits beside an `assess`, a person really answered, and where none does, the `human` in that
sentence is a callable. `docs/safety.md` says so in as many words, because that is the page
somebody reads after a robot did something it should not have.

**With two threads printing at once the file's order can differ from the screen's by a line or
two.** The forward happens after `super().print` and the capture's lock deliberately never spans
it, so two threads that print at the same moment can reach the file in the other order. There is
one real case, the kill switch, which narrates from the thread that reads stdin. The obvious fix
is to hold a lock across the terminal write, and that is the one thing that cannot be done here:
`Live` already holds its own lock around the refresh thread's print, and taking ours first
inverts that order between the two threads and hangs the run. A line out of order is a cost worth
paying and a deadlock with a robot mid-verb is not. Nothing is lost either way, and
`transcript.jsonl` is timestamped and ordered whatever the screen did.

**On a narrow codepage the file can carry a character the screen could not.** `terminal.txt` is
opened UTF-8 and the console writes whatever the terminal's encoding can carry with
`errors="replace"` behind it, so a `cp1252` stdout shows `??` where the file has `日本`. The file
is the more faithful of the two, which is the better direction for this to fail in, and it is
still a difference between two things this ADR keeps calling copies of each other. The sink
borrows the printing console's `encoding` so that Rich picks the same glyphs for both, which
settles the box drawing and the ticks quackd chooses; it does not settle a character that came in
from a goal, a model or a robot.
