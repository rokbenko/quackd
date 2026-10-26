# ADR-0065: A person drives one robot, a command at a time

**Status:** accepted · **Date:** 2026-10-26 · Extends [ADR-0003](0003-three-loops.md) (a person's loop above the pilot's, which reaches the body through the same executor) and [ADR-0040](0040-a-discrete-stepper-in-front-of-the-model.md) (the stepper's labels and floors answer a person's line as well as a turn) · Implemented in `quackd/drive/` (`session.py`, `lanes.py`, `presets.py`, `attached.py`, `terminal.py`, `run.py`), `quackd/agent/decision/commands.py`, `quackd/agent/loop.py` (`stop_reason`) and `quackd/cli.py` (`quackd drive`) · Documented in [docs/guides/drive.md](../guides/drive.md)

## Context

Rok asked on 2026-10-25 for a command line that answers as close to real time as it can, after
research on a demo in which a decision model picked one of a duck's trained moves in a fraction
of a second. quackd had no path from a person's command to a verb. `quackd run` is one serial
loop, observe, a model call, a verb, observe again, and on the README's arm run the body first
moved on the third model call, after 23.2 seconds of them. A person who says "forward" has already chosen
the call, and a model asked to choose it again adds seconds and a bill and nothing else. Most
commands a person gives a body name one of its verbs, a value or two, or a walk. The rest are
goals with several steps in them, which is what the pilot is for.

## Decision

1. **`quackd drive` holds one robot and reads a line at a time**, from the keyboard or from a
   pipe or a file. It resolves the robot as `serve-mcp` does (`fleet_from_flags`) and holds it in
   the same `RobotSession`, so the connect, the rest pose, the heartbeat and the close are the
   ones every MCP session has.
2. **A line goes through lanes, and the first that takes it wins.**
   - The brake: a line with no letter or digit in it, or one whose first word, read up to a
     hyphen, is a brake word. It is pure and synchronous, matched by whole word, so "quack" never
     brakes, and it errs on the side of braking, since a false brake costs a line typed again.
   - The session's own words: `help`, `status`, `quit` and `exit`.
   - The exact lane, local and pure. It reads the stepper's own labels for every closed-set call
     (`kick(leg=left)`), a verb's name with its values in any order, a bare name, the aliases,
     the presets, a call written out, and a string verb taking the rest of the line. A call
     written out is parsed with `ast` and its values read as literals, never evaluated. The
     executor validates and clamps every number, as it does anybody's.
   - The fast lane, only where a decision LLM is named. It puts one closed question over every
     offered call and preset, plus `pilot` and `unclear`, and is bounded by the stepper's own
     second (`TIMEOUT_S`) and held to its floors (`FLOORS`) and the `.duck`'s confirm gates, as
     a run's stepper is. Below a floor it asks the person, and a question nobody could answer
     hands the line to the pilot. Nothing moves on a guess. Each question is a `fast` row.
   - The pilot: anything else is a goal.
3. **A preset is the walk `move()` takes, scaled to the body's clamp.** It has no number of its
   own: the share of each clamp is `move`'s default speed over the schema's bound, a walk lasts
   `move`'s default second, and a turn covers `search_scan`'s default step. Each preset is
   validated through `MoveParams`, so a body whose clamps `move` itself would refuse is offered
   no such walk. A body without `move`, or that does not move about, gets none. On the Microduck
   `forward` is `move()` exactly.
4. **One mover at a time.** A command that moves the body ends whatever moves it now, a verb or
   a goal, and waits for it to stop before it sends anything ([ADR-0067](0067-a-stop-ends-the-verb-beside-it-and-a-newer-command-ends-the-one-it-finds-moving.md)).
   A command still waiting its turn when a newer one comes is dropped, so only the newest runs,
   and a verb that moves nothing runs alongside.
5. **A goal borrows the body the session holds.** It is a whole `AgentLoop` with its own
   executor, verdict and run directory under `goals/`. It runs on an `AttachedBody`, whose
   connect hands back the manifest, whose close and heartbeat do nothing and which has no rest
   pose, nor any way to set the arm's segment length, so a goal never reconnects, folds or lets
   go of the body between two commands. A goal runs inside the session's contract:
   - it gets the body's safe verbs, as `quackd run --goal` gives one, behind any confirm the
     session's `.duck` asks for them;
   - its budgets are no more than the session has left, its steps and model calls are charged to
     the session's, and no goal starts once the session's are spent;
   - the session's abort rules are its own, the goal's kept only where the session has none of
     that kind;
   - it takes the session's `--dry-run`.

   The session's own abort, its heartbeat failing above all, ends it, and `stop_reason` says why
   in the goal's own record.
6. **A session drives under a contract.** With `--duckfile` it is that file's, its body not run.
   Without one it is a contract of the session's own:
   - every verb that is not dangerous, as a contractless session allows;
   - the battery rule a goal is held to;
   - the schema's own ceilings for its budgets, since a person drives for longer than one
     task's defaults last.

   It leaves out the rule that ends a run on three failures in a row, which would end a session a
   person was still driving.
7. **The keyboard goes through prompt_toolkit, and Enter brakes.** `prompt_async` reads on
   drive's own loop, so a line can be typed over a walk, and `patch_stdout` keeps the record's
   lines above the prompt. prompt_toolkit is a core dependency, imported by this front end alone.
   - An empty Enter brakes, and so does Ctrl-C, which keeps the session. A second Ctrl-C within
     `QUIT_AGAIN_S` ends it.
   - A question is answered by `y` or `n` alone, so a brake is never taken for a no, and a lone
     `y` or `n` with no question waiting answers nothing.
   - The prompt needs a terminal for its output as well as its input, and without one drive reads
     lines one at a time, as from a pipe.
   - The kill switch is not installed, since its `q` would end a session at "quack".
   - A pipe reads a line, waits for its command to end and reads the next, and answers every
     question no.
   - A session ends what is still moving the body before it folds and lets go of it.
8. **The record is a run's.** It is a run directory of its own, `runs/<stamp>-drive[-name]/`,
   opened by `run_start` once the robot has connected and closed by `run_end`, whose outcome says
   how the session ended, so `quackd log` replays it. Between them are `command`, `fast`,
   `routed`, `started`, `ended`, `brake`, `goal_start` and `goal_end`. The summary's `drive`
   block counts the lanes and outcomes, and gives the waits to a call's start and to its first
   motion by lane, and from a brake to its stop, as `quackd.latency`'s nearest rank.
9. **A body that cannot see a fall is driven only with somebody watching it.** On a body with
   no fall detection and no way to get up, drive asks once, before the first thing that moves
   it, whether the person is watching, as `quackd run` asks before its first leg moves. A no
   refuses that command and the next asks again, `--yes` answers yes, and a pipe, which nobody
   answers for, is refused.

## Why not

- **A prompt in front of `quackd run`'s pilot.** Every command would wait on a model call, and
  the point is that a line which names a call needs none.
- **A model reading every line.** The exact lane answers in microseconds, offline, for nothing,
  and the fast lane is there for the lines it cannot read.
- **`input()` or Typer's prompt.** Neither reads while a verb runs, and typing over a walk is
  the whole of a brake that waits on nothing.
- **A goal that connects its own body.** A run folds its arm and lets go of it at its end, so an
  arm would fold and go limp between two commands.
- **Numbers for the presets.** A number of its own would be wrong on the first body whose
  clamps differ, and `move`'s own defaults are already quackd's word for a walk.

## Consequences

- Nothing here has driven a real robot. Drive has run against the mock Microduck and the
  scripted pilot, and its keyboard against prompt_toolkit's own pipe input. The prompt is untried
  in PowerShell under conhost, in Windows Terminal and in VS Code's terminal.
- The fast lane's floors are the stepper's, unmeasured on this question. The `fast` rows, with
  the person's yes or no to each question in the `routed` row after it, are the data to measure
  them by.
- `quackd log` replays the verbs and does not yet draw drive's own lines.
- A goal's pilot takes `--llm` alone, without the vendor flags `quackd run` takes, and a session
  keeps no memory.
- A session whose link has failed refuses every call but `stop`, as a run's executor does, and
  ends any goal it is flying.
