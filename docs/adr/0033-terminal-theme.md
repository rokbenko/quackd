# ADR-0033: The terminal has a house style, and a way out of it

**Status:** accepted, amended · **Date:** 2026-09-12 · Numbered 0033 on 2026-09-13, because two branches that merged the same day had both taken 0031 · Amends [ADR-0029](0029-tracing.md) (its "lines are ASCII first" rule becomes "the MCP lines are ASCII; a terminal asks its own stream") · Extends [ADR-0001](0001-language.md) (`typer` + `rich` were always the CLI stack; this is what they are used for) · Documented in [architecture.md](../concepts/architecture.md#trace)

**Amended 2026-09-21 by [ADR-0042](0042-the-log-is-the-whole-screen.md):** the trace this ADR draws is now the log, and `quackd trace` below is `quackd log`; the old spelling works for one release and goes in 0.12. The consoles do one thing more: `make_console` returns a `ui.TeeConsole`, so everything printed through `ui.console` and `ui.err_console` is copied into the run directory's `terminal.txt` with no colour and no control characters in it, and the glyph half this ADR picks off the stream is what that copy shows too. The house style is unchanged, and so is the split between the two surfaces. One rule below settled something for the new work instead of changing: `prompt` was already the label of the system prompt block, so the line a person's answer gets is `asked`.

## Context

quackd's colours grew where they were needed. A `[green]` in `validate`, a `Table` in
`list-verbs`, an emoji in the run header, a dim line in `memory show`, thirteen tables in a
row in `doctor`. Nothing said which green meant *this worked* and which meant *this is
installed*, and nothing owned the question, so each new command answered it again.

Four things were wrong rather than merely inconsistent.

- **Every decoration was a markup string.** `f"[green]{ver}[/green]"` is a colour and a
  value welded together, which is why `doctor` could not have a `--json` at all: there was
  nothing underneath the styling to serialise. It is also why `--live` advertised an install
  called `quackd` rather than `quackd[live]`: Rich read the extra as markup and ate it. Nine
  call sites defended against that with `escape()`; the tenth did not have to be found.
- **The glyphs did not survive the stream they were written to.** A redirected stderr on
  Windows is cp1252, and `·`, `—`, `✓`, `🦆`, `°` and the adapter registry's status emoji all
  arrived as `?`. ADR-0029 answered this for the trace by writing ASCII everywhere and
  letting colour carry meaning. That is the right answer for a stream that cannot do better
  and a tax on every terminal that can.
- **The two ends of a run were the hardest things to find in it.** The header was one line
  of middle dots; the verdict was three lines under a screenful of trace.
- **Nothing had a machine-readable form.** `discover` had `--json`. Reading anything else
  from a script meant parsing a wrapped table cell.

## Decision

- **One module owns the vocabulary.** `quackd/ui.py` holds the styles, the glyphs, the
  consoles, the panels and the tables. A new command inherits the house style instead of
  inventing a third one.
- **Text is data.** Everything printed is built as `rich.text.Text`, never as a string with
  a tag in it. A model's reasoning, a robot's manifest and an error naming
  `quackd[anthropic]` all carry square brackets, and none of them is parsed. `escape` is now
  unused in `cli.py`: there is nothing left to escape.
- **Glyphs have an ASCII half, chosen from the stream.** `ui.glyphs_for(console)` reads the
  console's own encoding. Panels and tables decide *when they are drawn*, not when they are
  built, because a replay goes to stdout, a run narrates to stderr and a test hands round a
  buffer of its own. The same applies to quackd's own prose: a degree sign it wrote becomes
  ` deg` rather than `?`.
- **The terminal trace is a different surface from the MCP trace.** `render_lines` and its
  padded `->` / `<-` columns are a contract with a model and are frozen, byte for byte, by
  `tests/golden/log_lines.json`. The terminal renders the same `LogLine`s its own way:
  a glyph in a gutter, a word in the label column, a rule per step, the system prompt as a
  block. This is the amendment to ADR-0029: *the reader that cannot do better still gets
  ASCII, and it is now the stream that says so rather than the renderer assuming it.*
- **A run opens and closes with a panel**, and the verdict is coloured by its outcome.
  `quackd trace` prints the same panel from the transcript, so a replay ends the way the run
  did.
- **Chrome goes to stderr, answers go to stdout.** Spinners and the status line are on
  `err_console`; tables and verdicts are on `console`. `quackd list-adapters > adapters.txt`
  gets the table and nothing else.
- **A status line where a command waits.** A run spends nearly all its wall clock inside two
  calls, a model deciding and a verb steering a robot, and said nothing until each finished.
  A transient line at the bottom names what is being waited on and how long it has been.
  It exists only when stderr is a terminal somebody is watching, and it steps aside for a
  confirmation prompt, because a live region redirects stdout and would swallow the question.
- **`--json` and `--no-color` are the way out.** `--json` on `validate`, `list-verbs`,
  `list-adapters`, `doctor` and `discover` prints one object per line on stdout and nothing
  else, keeping the exit code it would have had. `--no-color` sets `NO_COLOR` as well as
  quackd's own consoles, because Typer builds a console of its own for every `--help`.

## Consequences

- **`doctor` had to be split to get a `--json`.** `collect` answers in dataclasses with no
  styling in them, `render` decides what they look like, `to_dict` is the report with no
  renderer at all. That split is what made the sections, the verdict and the progress
  callback possible too, and it is the shape any future report-shaped command should take.
- **The plain renderer is now guarded by a golden.** 72 named cases covering every branch,
  including the four kinds that draw nothing. A terminal redesign that moved the MCP bytes
  would have been invisible before; now it fails one test that says which branch.
- **The terminal output is not the transcript.** It never was: the console truncates
  thinking and the MCP result caps at thirty lines. It now also respells what a codepage
  cannot carry and lifts the budget header out of the observation body. The transcript has
  every byte, always, and `--json` has the report.
- **Tests that pinned `-> sound` now pin `send sound`.** The terminal's labels are its own.
  The MCP tests and the golden are unchanged, which is the point of the split.
- **Rich is doing more work per line.** A trace line is a `Text` with three spans rather
  than a string. The burst coalescing that bounds the line count is unchanged, and the
  status line refreshes at 4 Hz, which is what the trace already cost in a terminal.
