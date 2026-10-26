# ADR-0066: A person's command is the verdict for its own call

**Status:** accepted · **Date:** 2026-10-26 · Amends [ADR-0032](0032-datasheets-and-the-verdict.md) (a call a person named clears the gate before motion by itself) · Implemented in `quackd/safety.py` (`Source`, `PERSONS`, the verdict gate), `quackd/log.py` (`A_PERSON`, `A_PIPE`), `quackd/drive/session.py` (`Answered`) and `tests/test_verdict.py` (the guard on who may say a call is a person's) · Documented in [docs/guides/drive.md](../guides/drive.md#your-command-is-the-verdict-for-its-own-call)

## Context

[ADR-0032](0032-datasheets-and-the-verdict.md) holds every pilot to a verdict before the first
verb that moves the body. The pilot judges the task against the body's datasheet, and nothing
that moves runs until it has, because a model that never asked whether the body can do the task
will try anyway. In a drive session ([ADR-0065](0065-a-person-drives-one-robot-a-command-at-a-time.md))
a person names one call, "kick left". That judgement is the person's, made for that call, and a
model asked to make it again would cost seconds on every command. The gate cannot simply be
off, either: the record has to say who cleared each motion, and a pipe must never be recorded as
a person.

## Decision

1. **`Source` gains `person`, and `PERSONS` is `person` and `cli`.** A call from either clears
   the verdict gate by itself, for that call and the verbs it runs. The gate writes `verdict
   allowed` with "a person named this call, which stands for the verdict for it alone", or "a
   pipe", once, for the call itself: a verb it runs inside itself is covered by that row, as a
   pilot's nested verbs are by its verdict. `Executor.verdict` is never written, so the next call
   from anybody else is gated as before, and a goal's pilot, on an executor of its own, judges
   its own task.
2. **A call the fast lane took without asking says whose reading it was.** Rok decided that a
   call picked for a person clears the gate as one they named, and that the record says who
   chose it. `run_verb` takes `picked_by`, the decision LLM that read the person's words as the
   call, and under `--decision-llm jev` the gate writes "jev read what a person typed as this
   call" in its place. A call the person said yes to, `did you mean ...?`, is theirs.
3. **Only drive says a call is a person's.** A test reads every module under `quackd/` and
   refuses `source="person"` or `source="cli"` outside `quackd/drive/`. The loop passes `agent`
   and `decision`, MCP passes `mcp`, and the courtesy handler passes `peer`.
4. **A line typed at the keyboard is a person's, and one from a pipe or a file is `cli`.** Both
   clear the gate for their own call, and the record names them apart.
5. **A confirm gate still asks.** The executor asks its confirm synchronously, and a session
   cannot block the loop that reads the keyboard on a question. So the session asks first, in
   the executor's own words, the ones its `prompt` row quotes, and hands the answer to the call
   through a `ContextVar`, which the executor's `confirm` reads. It counts as a person's answer
   (`asks_a_person`) only where a person gave it. `--yes` answers by its standing rule. A pipe
   without `--yes` is answered no, since drive reads a pipe's lines as commands and never as
   answers.
6. **An answer is its own call's alone.** It answers that call's confirm once, and a verb the
   call runs inside itself, `approach_and`'s `then`, is answered by the standing rule. Read as
   the nested verb's answer too, a yes to a composite cleared a dangerous verb nobody had been
   asked about, and the record wrote down a question put to a person that nobody had put.
7. **A dangerous verb never runs in drive**, however it is named, and not inside another call
   either. Only a person answering at a terminal in `quackd run` may clear one.

## Why not

- **A verdict for the whole session.** A `feasible` recorded once would clear every later call,
  a pilot's among them, on a judgement nobody made about them.
- **The pilot's verdict for each command.** Seconds and a model call per command, for a
  judgement the person had just made.
- **No gate in drive.** The record would not say who cleared a motion, and a pipe would clear
  it in a person's name.

## Consequences

- The verdict gate's record has a third way to allow a call: a person's or a pipe's own word,
  or a decision LLM's reading of it.
- A verb that needs a yes, run inside a call a person named, is answered by `--yes` alone,
  since drive cannot stop the call to ask.
- A composite a person named runs its nested verbs under the same clearance, since a nested
  call inherits its outer call's source.
