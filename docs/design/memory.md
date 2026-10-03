# Design: quackd 0.6, a run that starts with what the last one learned

**Status:** implemented in 0.6.0 · **Branch:** `feat/memory-between-runs` (#5), `fix/max-minutes-provider-deadline` (#3) · **Shipped:** 2026-09-04

> **Read on 2026-09-13:** the decision below to key a memory file by `adapter:backend` is
> still what an unregistered run does, and still for the reason given. 0.9 added a finer key
> beside it: a robot registered with `quackd robot add` keys by the name you gave it, so two
> real ducks on one desk no longer share a file, which is the case this table's reasoning did
> not separate ([ADR-0034](../adr/0034-registered-robots-and-pilot-flocks.md),
> [registry.md](../guides/registry.md)).

## Why

Every quackd release so far built a pilot with no past. The transcript recorded each prompt,
each tool call, each frame and each verb result, wrote a summary, and then the process
exited and nothing ever read any of it again. A duck that had found the ball behind the sofa
three times searched the whole room a fourth time, and a `.duck` that had failed the same way
on six runs failed the same way on the seventh.

Nothing in the repository said so. The README's Limitations section listed eleven honest
gaps and this was not among them, which is its own small failure: a gap nobody has written
down is a gap nobody is accounting for.

The thesis does not change. The LLM picks verbs, the robot's own controllers move, quackd
enforces the contract. What changes is what the model knows when it starts picking.

## What this release is not

This is the first release built on other people's pull requests, so the design below is
[@Bayway](https://github.com/Bayway)'s (ADR-0025, PR #5) and
[@r0jin](https://github.com/r0jin)'s (PR #3), not this repository's. What this repository
added was the review, and the section after next is what the review found.

## The decisions

Recorded in [ADR-0025](../adr/0025-memory-between-runs.md); the short version:

| Decision | Why |
|---|---|
| One append-only JSONL file per robot, keyed `adapter:backend` | A note about a 2 m cartoon arena is wrong for a living room. The simulator and a real duck must never share notes, and `adapter:backend` is the key both `quackd run` and `serve-mcp` already have |
| Two kinds of entry, one chosen and one automatic | A model that has to be asked to remember will forget, and a log nobody curates is the only kind that stays honest. Notes are what the pilot decided to keep, episodes are what happened whether it noticed or not |
| `remember` is a meta tool, not a verb | ADR-0017 says the vocabulary comes from the manifest. A verb that moves nothing does not belong in a manifest, so `remember` sits next to `declare_success`, costs an LLM call and no step, and is absent from every allowlist |
| Newest wins, capped, no retrieval | A vector store means an embedding model in the default install, a second thing to keep in sync, and a search index over a handful of sentences about a toy duck. Twenty notes and five episodes fit in the prompt and cost nothing to maintain |
| The starter ducks ask for the note inside the strategy | A prompt-level hint alone was ignored: Qwen 2.5 Coder 14B read the memory block and never wrote to it across four runs. A `remember` in the last numbered step was followed on the first |
| The executor never reads memory | A note is text a model wrote. Letting it reach the allowlist, the budgets or a confirm gate would make the safety layer argue with the thing it exists to distrust |
| Memory off removes the heading, the tool and the episode | So the scaffolding's cost is auditable against any previous transcript rather than believed to be small. It does *not* make the whole prompt byte-for-byte what it was, because the starter ducks' own bodies now ask for a `remember` either way (see below) |

## What the work found

Both pull requests were reviewed against the code rather than accepted on their checklists,
which is the only reason this section exists. Neither had ever been run by CI: they are fork
PRs, and this repository requires approval to run workflows on those. Fifteen defects in the two
contributions, two of them blockers, and six claims that had gone stale on `main` before
either arrived.

### In the two contributions

- **PR #5's stated motivation was false.** Its description and ADR-0025 both said the
  README listed this gap among its limitations. It never has, on `main` or on the branch.
  The sentence would have shipped in a permanent architecture decision record.
- **The file you are invited to hand-edit could take the command down.** A line that is not
  JSON is skipped, as documented. A line that *is* JSON but not an object (`"a note"`, `17`,
  `[]`) raised `AttributeError` out of every reader, because only `JSONDecodeError` was
  caught. The promise was in the code comment, `docs/memory.md` and the ADR, three times.
- **`--dry-run` wrote permanent notes.** The end-of-run episode was guarded; the `remember`
  tool was not. So the one mode whose entire contract is "sends nothing" could leave a
  permanent conclusion drawn from verb results it had invented.
- **A refreshed note sorted as the oldest.** Repeating a sentence refreshes its timestamp and
  left it where it sat, and both the prompt's newest-first window and the cap read *file*
  order. The note the model had just chosen to repeat was the first one evicted.
- **`quackd memory show --raw` did not print the file as is**, which is its only job: it
  went through Rich, so a note reading `the ball is [bold]behind[/bold] the sofa` printed as
  `the ball is behind the sofa`. A note is text a model wrote and can contain anything.
- **`quackd memory --robot <bad spec>` printed a Python traceback**, where every other
  command taking `--robot` answers in one line through `_fail`. The whole command group
  shipped with no test, which is how two of its three subcommands were wrong.
- **One assertion was `A or B` where `A` was never true.** The highlight carries the
  robot's own sound tone, not the text, so the `==` half of the disjunction never held and
  the test fell through to a `startswith` that checked almost nothing.
- **A test took 14.6 s here, on a matrix that runs it four times,** to prove that a
  400-entry cap drops episodes before notes. Every write re-reads and rewrites the whole
  file, so the test was quadratic in the cap. A cap of 12 proves it in 0.16 s, and the
  rewritten test also asserts *which* entries survive, which the original never did.
- **The MCP tool count went stale in six places**, of which the PR updated one. Two README
  sentences, `docs/architecture.md`, a `--robots` help string, a module docstring and a test
  docstring all still said six.
- **The README's Performance figure became wrong by default.** "The system prompt and the
  eight tool schemas together are about 5.7 k characters" was measured and accurate. With
  memory on it is nine schemas and 7.0 k, and the sentence now carries the 6.0 k
  `--no-memory` figure beside it. Both re-measured, not estimated.
- **ADR-0025 promised the memory-off prompt was "byte-for-byte what it was".** It is not, for
  any of the seven ducks the same PR rewrote: a duck body is part of the prompt.
- **`RobotMemory`'s docstring claimed it was "safe across a CLI run and an MCP server".** It
  is a read-modify-write of the whole file with no lock, so two simultaneous writers lose one
  of the two notes. Renaming a temporary file over the old one makes it *atomic*, which is
  not the same claim. The docstring now says which of the two it is, and why that is the
  accepted price of a file you can also edit in a text editor.
- **The prompt told the model `remember` "is free".** It costs an LLM call, out of the same
  budget every other turn draws on, and the ADR and docs both said so.
- **With `--no-memory` the MCP server still told the pilot to call `robot_recall` early**, a
  tool that would answer "memory is off": an instruction to waste a turn.
- **ADR-0025 said "one memory per robot in the fleet".** The key is the body, so two fleet
  members that are the same `adapter:backend` share a file. That is the right behaviour and
  the wrong sentence.

### Already stale on `main`

- `docs/architecture.md` still described `mcp_server.py` as carrying "the eight `duck_*`
  tools kept as aliases", which 0.5 removed, and had not been updated for memory at all.
- The README's own architecture diagram listed four adapters and omitted `open_duck` and its
  `bridge` backend, five releases after a guard was written for exactly this class of
  staleness, because that guard reads the phrase "N adapters" and not a list.
- Three documents said one quackd daemon runs on a robot, while `bridge/open_duck/` has
  shipped two since 0.5, and `docs/architecture.md` contradicted itself about it two tables
  apart.
- `docs/duck-spec.md` called `max_minutes` a cap without saying that a verb already running
  is not interrupted, so a run can overshoot by that verb's own timeout.
- `_deprecated()` in the CLI was dead from 0.5's `--transport` removal, `quackd --help`
  still introduced the tool as a way to "pilot a Microduck" five adapters later, and
  `_pick_default`'s docstring justified its rule with the `duck_*` aliases.
- **The README said no live local server had ever been run.** PR #5's evidence *is* a live
  local server run. The claim was true when written and this release makes it false, so it
  now says whose machine it was and that no transcript from it is in the repository. That is
  the first local-model evidence this project has had, and it arrived in a footnote.

Each of these is fixed, and the ones no test could see became tests. A living document *or a
Python string a user reads* may not claim a number of `robot_*` tools the server disagrees
with, which is 0.5's `--transport` lesson finally applied to counts as well as flags. The key
the CLI computes for a robot's memory file must equal the key the MCP server computes, for
all eight adapters and all twenty-two backends. Every way of corrupting the memory file by hand
is skipped rather than fatal. And the `quackd memory` command group has tests at all, which
is how two of its three subcommands came to be wrong.

## What is still not true

- **No cloud model has ever called `remember`.** The scripted pilot has no script for it, so
  `--llm fake` accumulates episodes and never writes a note. The only evidence a model
  uses the tool at all is the contributor's Qwen 2.5 Coder 14B runs on `find-and-kick`,
  seeds 5 and 6.
- **`--no-memory` does not silence the ducks.** The seven starter tasks ask for a `remember`
  inside their strategy, and that text is in the prompt whether or not memory is on. A pilot
  that follows it gets a clear refusal costing one LLM call and no step. Fixing this properly
  means the duck body knowing about a runtime flag, which is a worse coupling than the wasted
  call.
- Nothing here has run on a robot of any kind, as before.

## Acceptance

- A second run's system prompt contains the first run's note and the first run's outcome,
  asserted from the transcript rather than from the file.
- `--no-memory` writes no file, offers no `remember`, and puts no memory heading in the
  prompt.
- A dry run writes no episode.
- The key the CLI computes and the key the MCP server computes agree for all eight adapters
  and all twenty-two backends, which is what makes "a note saved from Claude Desktop is read
  by the next `quackd run`" true rather than intended.
- A late provider answer ends the run as `budget`, and an in-budget one is still processed.
- The built wheel's long description has no relative links left in it.

## Only a human can do these

Run any of this against a real robot, and run `remember` against a cloud model. The first is
the same wall every release has hit. The second needs an API key this repository has never
had.
