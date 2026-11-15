# Memory: what a robot keeps between runs

Until 0.5 every run started from nothing. The transcript recorded every prompt, tool call
and frame, and the next run never read it. A pilot that had found the ball behind the sofa
three times in a row would search the whole room a fourth time.

Since 0.6 each robot has a small **memory file** that the loop reads at the start of a run
and appends to at the end. Two kinds of entry live in it:

| Kind | Who writes it | Example |
|---|---|---|
| **note** | the LLM, with the `remember` tool (or you, with `roboglot memory add`) | `the ball is usually near the left wall` |
| **episode** | Roboglot itself, at the end of every non-dry run | `open-duck-scout: success — walked up and reported (2 steps) · go_to: reached the ball: ~0.28 m away, bearing +0°; say: said 'ball at 0 degrees left, about 0.3 m' as [chirp]` |

A run that ended `infeasible`, where the pilot judged the task beyond this body and
nothing moved, is remembered the same way and with the same reason, hint included: the next
run on the same robot is told what was already found not to fit it.

At the next run the newest notes (up to 20) and episodes (up to 5) are rendered into the
system prompt under *What you remember from earlier runs on this robot*, and the LLM is
told how to add to them. Nothing else changes: the allowlist, budgets and confirmation
gates are exactly what they were.

## Where it lives

One JSONL file per robot, keyed by `adapter:backend`, so the Microduck's mock and a real
Microduck never share notes (a note from a run on the mock is wrong for your living room):

```
~/.roboglot/memory/microduck-mock.jsonl
~/.roboglot/memory/microduck-jsonrpc.jsonl
~/.roboglot/memory/lerobot-mock.jsonl
```

The key is the body, not the name you gave it, so two members of one team that are the
same `adapter:backend` share a file. That is the same rule that keeps `microduck:mock` and
`microduck:jsonrpc` apart, read the other way round.

**Unless you have registered it.** `roboglot robot add duck-a microduck:jsonrpc` gives that one
robot a name, and a run by that name keys its memory by the name instead:

```
~/.roboglot/memory/duck-a.jsonl
~/.roboglot/memory/duck-b.jsonl
```

So two real ducks on one desk keep separate notes, which the rule above could not express.
A registered name may not collide with an adapter name or with one of the `adapter-backend`
slugs above, so the two schemes never meet in one file
([guides/registry.md](registry.md), [ADR-0034](../adr/0034-registered-robots-and-pilot-teams.md)
amending [ADR-0025](../adr/0025-memory-between-runs.md)).

Override the directory with `--memory-dir` or `ROBOGLOT_MEMORY_DIR`. Turn it off for one run
with `--no-memory`. The file is plain text, one JSON object per line, meant to be read and
edited by hand; a line that is not JSON is skipped, not fatal. It is capped at 400 entries,
and the oldest **episodes** go first, because notes were chosen on purpose.

## The `remember` tool

When memory is on, the LLM gets one more tool beside `assess_task`, `declare_success` and
`declare_failure` (and `tell`, in a team of pilots):

```jsonc
{"name": "remember", "arguments": {"text": "kick with the right leg from 0.25 m works", "tags": ["strategy"]}}
```

It is deliberately cheap: it moves nothing, it counts as an LLM call but **not as a step**,
and the same sentence twice refreshes the old note instead of duplicating it (and moves it
to the newest position, because newest is what `recall` and the cap both read). Under
`--dry-run` it saves nothing and says so, like every other intent a dry run refuses to
send: an inert run must not leave a permanent conclusion drawn from results it invented. The reply
lands in the next observation like any verb result (`last verb remember: ok — remembered
for future runs: …`) and in the transcript as a `memory` event, so a run's provenance still
shows every fact it saved.

## The starter tasks ask for it

Telling the LLM about `remember` in the prompt is not enough for a small local LLM: in
the runs this feature's contributor did, Qwen 2.5 Coder 14B read the memory block and never
wrote to it ([design/memory.md](../design/memory.md) records those runs). What works is
putting the call **inside the numbered strategy** of the `.glot` body, right before the
declaration (`5. When the ball has moved ≥ 0.3 m, \`remember\` where you found the ball,
\`quack\` once and declare success.`), plus a short *Memory* section saying what is worth
keeping. The solo starters do that, except the bring-up lookouts (`xlerobot-lookout`,
`alohamini-lookout`, `toddlerbot-lookout`, `lerobot-lookout`); `--goal` runs get the same line
while memory is on, and none under `--no-memory`, which offers no `remember`. `team-hello` is a
team of pilots, whose members are whole agent loops with a memory file each, so a note there would
work. It does not ask for one because it is a radio check. `hello-world` is left alone: it is a
smoke test that says "do not do anything else". Write your own task files the same way.

## The CLI

```bash
roboglot memory show                              # with one adapter installed, its mock's memory
roboglot memory show --robot microduck:jsonrpc    # the real duck's
roboglot memory show --raw                        # the JSONL as is
roboglot memory add "the charger is under the desk" --tag place
roboglot memory clear --robot microduck:mock      # asks first; --yes to skip
roboglot run find-and-kick --no-memory            # one fresh run, file untouched
```

A line with no `--robot` takes the one adapter installed here, on its `mock`. Where several are
installed it is refused, and the refusal lists them.

`roboglot run` names it in the header panel it opens with (`memory  3 notes, 5 earlier runs`
and the file it came from) so you always know what the pilot was told.

## Over MCP

`roboglot serve-mcp` gives every `adapter:backend` in the team its own memory behind two
tools:
`robot_recall(robot?)` returns the notes and recent episodes (the server's instructions
tell the LLM to call it early), `robot_remember(text, tags?, robot?)` saves one note.
`--no-memory` and `--memory-dir` apply to the server too. A note saved from Claude Desktop
is read by the next `roboglot run` on the same `adapter:backend`, and the other way round.

## What it is not

- **Not a learning loop.** The robot's skills are still its own ONNX policies; memory
  changes what the *pilot* knows, not what the body can do. Learned verbs (policies
  trained from LLM-written rewards) are a separate, unshipped v2 feature
  ([concepts/learned-verbs.md](../concepts/learned-verbs.md)).
- **Not retrieval.** There is no embedding, no search: the newest entries win, and the cap
  keeps the prompt small.
- **Not where things are, on its own.** A body that says where it is keeps named places beside
  this file, `<slug>.places.jsonl`, and a note saved there keeps where the robot stood: recalled,
  it says how far that was from the nearest place and from here, "saved 0.3 m from sofa, 2.1 m
  from here", or that its frame has ended since ([concepts/places.md](../concepts/places.md)).
  On a body that does not, write where things are as a few notes.
- **Not shared between bodies.** By design, where a body means an `adapter:backend`. Use
  `roboglot memory add` on the other robot if a fact really transfers. The same rule decides a
  [team](team.md): members key by body, so two of one kind share a file unless they are
  registered, and what one needs another to know *during* a run it says with `tell` rather
  than through memory, which the other pilot would not read until its next run anyway.
- **Not written by the scripted pilot.** `--llm fake` has no `remember` in its script,
  so it accumulates episodes and never a note. Notes have been exercised by a local LLM, in
  the runs [design/memory.md](../design/memory.md) records, and by one cloud LLM on a real
  robot: `gpt-6-astra` called `remember` in seven of its twelve runs on the SO-101 arm on
  2026-09-15, and five distinct notes came out of those seven, because the same sentence
  twice refreshes the first rather than saving a second. The last run of that afternoon read
  all five back out of its own prompt.
- **Not trusted.** A note is text the LLM wrote; the executor never reads it. Safety
  lives in the contract, as before ([concepts/safety.md](../concepts/safety.md)).
