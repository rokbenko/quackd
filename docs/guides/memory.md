# Memory: what a robot keeps between runs

Until 0.5 every run started from nothing. The transcript recorded every prompt, tool call
and frame, and the next run never read it. A pilot that had found the ball behind the sofa
three times in a row would search the whole room a fourth time.

Since 0.6 each robot has a small **memory file** that the loop reads at the start of a run
and appends to at the end. Two kinds of entry live in it:

| Kind | Who writes it | Example |
|---|---|---|
| **note** | the model, with the `remember` tool (or you, with `quackd memory add`) | `the ball is usually near the left wall` |
| **episode** | quackd itself, at the end of every non-dry run | `find-and-kick: success — ball displaced 0.49 m (4 steps) · search_scan: ball found at bearing 18° left; kick: ball moved 0.49 m` |

A run that ended `infeasible`, where the pilot judged the task beyond this body and
nothing moved, is remembered the same way and with the same reason, hint included: the next
run on the same robot is told what was already found not to fit it.

At the next run the newest notes (up to 20) and episodes (up to 5) are rendered into the
system prompt under *What you remember from earlier runs on this robot*, and the model is
told how to add to them. Nothing else changes: the allowlist, budgets and confirmation
gates are exactly what they were.

## Where it lives

One JSONL file per robot, keyed by `adapter:backend`, so a simulated duck and a real one
never share notes (a note about the cartoon arena is wrong for your living room):

```
~/.quackd/memory/microduck-sim2d.jsonl
~/.quackd/memory/microduck-mujoco.jsonl
~/.quackd/memory/microduck-jsonrpc.jsonl
~/.quackd/memory/lerobot-mock.jsonl
```

The key is the body, not the name you gave it, so two members of one flock that are the
same `adapter:backend` share a file. That is the same rule that keeps `microduck:sim2d` and
`microduck:jsonrpc` apart, read the other way round.

**Unless you have registered it.** `quackd robot add duck-a microduck:jsonrpc` gives that one
robot a name, and a run by that name keys its memory by the name instead:

```
~/.quackd/memory/duck-a.jsonl
~/.quackd/memory/duck-b.jsonl
```

So two real ducks on one desk keep separate notes, which the rule above could not express.
A registered name may not collide with an adapter name or with one of the `adapter-backend`
slugs above, so the two schemes never meet in one file
([registry.md](registry.md), [ADR-0034](adr/0034-registered-robots-and-pilot-flocks.md)
amending [ADR-0025](adr/0025-memory-between-runs.md)).

`microduck:sim2d` and `microduck:mujoco` are the pair that catches people out. They share an
arena (bar the cartoon's person, who is not in the physics one), a seed and a `.duck` file by
design, and they still keep separate files, because what a
pilot learns about how far to walk before kicking comes from the body, and only one of the two
has a gait.

Override the directory with `--memory-dir` or `QUACKD_MEMORY_DIR`. Turn it off for one run
with `--no-memory`. The file is plain text, one JSON object per line, meant to be read and
edited by hand; a line that is not JSON is skipped, not fatal. It is capped at 400 entries,
and the oldest **episodes** go first, because notes were chosen on purpose.

## The `remember` tool

When memory is on, the model gets one more tool beside `assess_task`, `declare_success` and
`declare_failure` (and `tell`, in a flock of pilots):

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

## The starter ducks ask for it

Telling the model about `remember` in the prompt is not enough for a small local model: in
the runs this feature's contributor did, Qwen 2.5 Coder 14B read the memory block and never
wrote to it (both cases are in [`assets/transcripts/`](assets/transcripts/)). What works is
putting the call **inside the numbered strategy** of the `.duck` body, right before the
declaration (`5. When the ball has moved ≥ 0.3 m, \`remember\` where you found the ball,
\`quack\` once and declare success.`), plus a short *Memory* section saying what is worth
keeping. The solo starters do that, except the bring-up lookouts (`xlerobot-lookout`,
`alohamini-lookout`, `toddlerbot-lookout`, `lerobot-lookout`); `--goal` runs get the same line. `flock-kick`
does not, because a coordinator flock's member is a state machine: it never runs the
deliberation loop and is never offered `remember`. `flock-hello` is a flock of pilots, whose
members are whole agent loops with a memory file each, so a note there would work; it does not
ask for one because it is a radio check. `hello-world` is left alone: it is a smoke test that says "do not do anything
else". Write your own ducks the same way.

A second model on a second server says the same thing from the other end. Qwen3-32B-AWQ on
vLLM, with `remember` sitting in strategy step 5, called it and saved where the ball was. The
very next run did not call it, which is also right: that fact was already in its prompt, and
the *Memory* section tells it to skip one the prompt already remembers. Those two runs are the
only place in this repository where a note written by one run reaches the next one's prompt,
and both ends are published ([local-llms.md](local-llms.md)).

## The CLI

```bash
quackd memory show                              # the Microduck simulator's memory
quackd memory show --robot microduck:jsonrpc    # the real duck's
quackd memory show --raw                        # the JSONL as is
quackd memory add "the charger is under the desk" --tag place
quackd memory clear --robot microduck:sim2d     # asks first; --yes to skip
quackd run find-and-kick --no-memory            # one fresh run, file untouched
```

`quackd run` names it in the header panel it opens with (`memory  3 notes, 5 earlier runs`
and the file it came from) so you always know what the pilot was told.

## Over MCP

`quackd serve-mcp` gives every `adapter:backend` in the flock its own memory behind two
tools:
`robot_recall(robot?)` returns the notes and recent episodes (the server's instructions
tell the model to call it early), `robot_remember(text, tags?, robot?)` saves one note.
`--no-memory` and `--memory-dir` apply to the server too. A note saved from Claude Desktop
is read by the next `quackd run` on the same `adapter:backend`, and the other way round.

## What it is not

- **Not a learning loop.** The robot's skills are still its own ONNX policies; memory
  changes what the *pilot* knows, not what the body can do. Learned verbs (policies
  trained from LLM-written rewards) are a separate, unshipped v2 feature
  ([learned-verbs.md](learned-verbs.md)).
- **Not retrieval.** There is no embedding, no search: the newest entries win, and the cap
  keeps the prompt small. If you need a map of your house, write it as a few notes.
- **Not shared between bodies.** By design, where a body means an `adapter:backend`. Use
  `quackd memory add` on the other robot if a fact really transfers. The same rule decides a
  [flock](flock.md): members key by body, so two of one kind share a file unless they are
  registered, and what one needs another to know *during* a run it says with `tell` rather
  than through memory, which the other pilot would not read until its next run anyway.
- **Not written by the scripted pilot.** `--llm fake` has no `remember` in its script,
  so it accumulates episodes and never a note. Notes have been exercised by two local models
  on two machines, and by one cloud model on a real robot: `gpt-6-astra` called
  `remember` in seven of its twelve runs on the SO-101 arm on 2026-09-15, and five
  distinct notes came out of those seven, because the same sentence twice refreshes the
  first rather than saving a second. The last run of that afternoon read all five back out
  of its own prompt.
- **Not trusted.** A note is text the model wrote; the executor never reads it. Safety
  lives in the contract, as before ([safety.md](safety.md)).
