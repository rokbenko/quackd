# Driving a robot: `quackd drive`

`quackd drive` drives one robot, one command at a time. A command is a verb of the body's own,
a preset walk, or a goal in your own words for the pilot. A line that names a call is read
without asking anybody, in under a millisecond on the capture below. An empty Enter brakes at
once, and a command that moves the body ends whatever is still moving it.

```bash
quackd drive --robot microduck:mock --llm fake
```

At a terminal it opens a prompt, `drive> `, and reads a line at a time without waiting for the
last one to finish, so you can type over a walk. Piped, it reads a line, waits for that command
to end and reads the next, which is how this session was captured, on the mock Microduck with
the scripted pilot:

```
$ printf 'stand\nforward\nturn left\nkick left\nsay good morning\nmove(vx=0.1, duration_s=0.5)\nlook around and tell me what you see\nquit\n' | quackd --no-color drive --robot microduck:mock --llm fake
drive    microduck: type a command, a goal, or help; Enter brakes
exact    stand (0.434 ms)
result   ok: already standing
exact    forward: move(duration_s=1, vx=0.15) (0.288 ms)
result   ok: walked vx=0.15 vy=0.00 wz=0.00 for 1.0s
exact    turn left: move(duration_s=1.571, vx=0, wz=0.5) (0.291 ms)
result   ok: walked vx=0.00 vy=0.00 wz=0.50 for 1.6s
exact    kick(leg=left) (0.283 ms)
result   ok: kicked with left leg
exact    say(text='good morning') (0.305 ms)
result   ok: said 'good morning' as [chirp]
exact    move(duration_s=0.5, vx=0.1) (0.46 ms)
result   ok: walked vx=0.10 vy=0.00 wz=0.00 for 0.5s
pilot    goal 1 (73.115 ms)
result   success: scripted pilot: nothing more to do
session  quit (0.226 ms)
result   ok: goodbye
record   runs\20261026-020711-drive
```

Each first line names the lane that took the line, what it came to, and the milliseconds from
the moment the line arrived to the moment that lane took it. Here that is quackd's own time,
since no line went to a decision LLM. A question put to you after it, `run it? [y/N]`, is
never counted in it, and the robot's link and the robot itself come after it.

## What a line can be

Every line goes through the lanes in this order, and the first that takes it wins.

1. **The brake.** An empty Enter, a line with no words in it, or a line whose first word is
   `stop`, `halt`, `brake` or `q`. By whole word: "quack" is a duck's word and never brakes,
   "stop now" and "Stop." do, and "don't stop" does not, since only the first word is read for
   it. The brake waits on nothing. It drops every line that has not started, whether still being
   worked out, asked about or waiting its turn. It ends every verb moving the body, stops a goal
   the pilot is flying, and sends the stop to the body directly, ahead of the `stop` verb, which
   reads the body's state first and runs after it for the record. A brake is never taken for the
   answer to a question: an empty Enter while `[y/N]` is showing brakes, and the question goes
   with the line that asked it.
2. **The session's own words.** `help`, `status`, `quit` and `exit`, each a line on its own,
   answered at once.
3. **The exact lane**, for a line that names a call outright. It knows these forms:
   - every call of the body's that is a choice rather than a value, labelled as quackd's
     decision stepper labels them, such as `kick(leg=left)`;
   - the same call as its name and its values in any order, `kick left`;
   - the bare name of a verb with nothing required, which runs it with its own defaults;
   - each alias of those (`walk`, `walk_to`, `get_frame`);
   - the presets below.

   A call written out, as `move(vx=0.2, duration_s=2)`, is read as literals and never
   evaluated: a name it calls, an operator or a starred argument is not a call this lane reads.
   A verb whose one required parameter is a string takes the rest of the line, so `say good
   morning` and `watch the red ball` are calls. The numbers you write are yours, and the
   executor validates and clamps them as it does anybody's.
4. **The fast lane**, only with `--decision-llm`. A line no verb names, "have a seat", is put
   to the decision LLM as one closed question: which of the body's calls or presets does the
   person mean, is it something for the pilot, or is it unclear? The line you typed goes wherever that
   LLM runs, a vendor's host for a hosted one, with what the robot is doing and a line about the
   body. The question is bounded
   by the stepper's own second and held to the stepper's own floors.
   - A call it is sure enough of runs. One it is less sure of is asked about first, `did you
     mean kick(leg=left)? [y/N]`.
   - `unclear` asks you to say it another way.
   - A line it says is for the pilot goes to the pilot, asked about first where it is less sure.
   - A question nobody could answer hands the line to the pilot: one declined, unreadable,
     failed or out of its second.

   At a pipe every such question is answered no. Nothing moves on a guess.
5. **The pilot**, for anything else. The line is a goal, flown by the pilot `--llm` names as a
   whole run of its own on the body the session holds. A goal has its own executor, its own
   verdict and its own run directory under the session's `goals/`. It gets the body's safe
   verbs, as `quackd run --goal` gives one, behind any confirm the session's `.duck` asks for
   them.

A dangerous verb is never run by drive, however it is named: only a person answering at a
terminal in `quackd run` may clear one.

## Presets

A word like `forward` or `turn left` means something on every body that walks, and nothing in it
says how fast or how far. So each body's presets are derived from its own clamps, with no
number of their own:

- a preset moves at the share of each clamp that `move`'s own default speed is of the schema's
  bound on it, which is half;
- a walk lasts `move`'s own default second;
- a turn covers `search_scan`'s own default step, 45 degrees.

A body whose clamps are faster than the schema's bounds gets no walk `move` itself would
refuse.

The Microduck walks forward as fast as the schema allows, so its `forward` is `move()` exactly.
It turns at most 1 rad/s, so a turn at half that takes 1.571 seconds to cover 45 degrees:

| Preset | The call on a Microduck |
|---|---|
| `forward` | `move(duration_s=1, vx=0.15)` |
| `back` | `move(duration_s=1, vx=-0.15)` |
| `turn left` | `move(duration_s=1.571, vx=0, wz=0.5)` |
| `turn right` | `move(duration_s=1.571, vx=0, wz=-0.5)` |
| `step left` | `move(duration_s=1, vx=0, vy=0.1)` |
| `step right` | `move(duration_s=1, vx=0, vy=-0.1)` |

`forward` and `back` may also be said as `walk forward`, `go forward` or `move forward`. A body
gets `step left` and `step right` only where it can step sideways. A body that does not move
about, an arm among them, gets no presets at all. A verb of the body's own spelled like a
preset beats it.

## Your command is the verdict for its own call

quackd holds every pilot to a verdict before the first verb that moves the body: the pilot says
whether the body can do the task at all, and nothing that moves runs until it has. A command
you type in drive is that judgement made by you, for that call. It runs without a verdict, and
the record says so, as `quackd log` replays the session above:

```
!  gate    verdict: allowed a pipe named this call, which stands for the verdict for it alone
```

Nothing is kept for the calls after it, so a goal's pilot still judges its own task before it
moves anything. Those lines came from a pipe, and a line typed at the keyboard says `a person`
there. Lines read from a pipe or a file clear the gate the same way, and are never recorded as
a person's.

A verb that needs a yes before it runs still asks for one. That is a verb of the `confirm`
class, or one a `.duck` lists under `confirm`:

- at the keyboard, you are asked;
- `--yes` answers yes, by its standing rule;
- a pipe without `--yes` is answered no. Drive reads a pipe's lines as commands and never as
  answers, where `quackd run` takes a pipe's next line for one.

## One mover at a time

A command that moves the body ends whatever is moving it, a verb or a goal, and waits for that
to stop before it sends anything, so the stop reaches the body before the new command's first
twist. A command still waiting its turn when a newer one comes is dropped, so only the newest
runs. A verb that moves nothing runs alongside: `observe`, `report state`, `say` and `quack`.

Ctrl-C brakes and the session stays. A second Ctrl-C within 1.5 seconds of the first ends the
session, as `quit`, `exit` and the end of input do. A session ends in order: whatever is still
moving the body is ended rather than waited out, and then the robot is stopped, folded where it
folds, and let go of.

## The contract

With `--duckfile`, the session drives under that file's contract: its allowlist, its confirm
gates, its abort rules and its budgets. Its body is not run. A file that leaves its budgets at
their defaults, 40 steps and 5 minutes, refuses every call but `stop` past either.

Without one, the session drives under a contract of its own:

- every verb the body has that is not dangerous;
- the battery rule a goal is held to, so a battery below 15% refuses every call but `stop`;
- a task's highest budgets, `max_steps: 1000` and `max_minutes: 180`.

It leaves out the rule that ends a goal on the same verb failing three times in a row, since a
person who kicks and misses three times has not asked for the session to end.

## The record

Each session is a run directory of its own, `runs/<stamp>-drive[-name]/`. Its `transcript.jsonl`
opens with a `run_start` and closes with a `run_end`, as any run's does, so `quackd log` replays
it. Between them it holds these lines:

- `command` for each line, and who sent it;
- `routed` for the lane that took it, with its milliseconds;
- `fast` for each question the fast lane put, with its confidence, its floor and its gate;
- `started` for each call as it starts, and `ended` for each line once what it came to is over;
- `brake` for every brake;
- `goal_start` and `goal_end` for each goal;
- the executor's own records of every verb, gate and intent.

Its `summary.json` has a `drive` block, this one from the session above:

```json
{
  "lanes": {"exact": 6, "pilot": 1, "session": 1},
  "outcomes": {"ok": 7, "success": 1},
  "to_start_ms": {
    "exact": {"n": 6, "p50": 0.5, "p90": 0.7, "max": 0.7},
    "pilot": {"n": 1, "p50": 73.3, "p90": 73.3, "max": 73.3}
  },
  "to_motion_ms": {"exact": {"n": 4, "p50": 1.3, "p90": 1.5, "max": 1.5}},
  "brake_ms": {"n": 0}
}
```

`to_start_ms` runs from a line to its call starting, `to_motion_ms` to the first intent other
than a stop that the body accepted for it, and `brake_ms` from a brake to the stop it sent. The first two are given
by lane, and every figure in milliseconds. Each goal the pilot flew is under
`goals/NN-what-you-typed/`, a whole run `quackd log` replays.

> [!WARNING]
> **Nothing here has driven a real robot yet.** Drive has run against the mock Microduck and
> the scripted pilot, and its keyboard against prompt_toolkit's own pipe input. Its prompt has
> not been tried in PowerShell under conhost, in Windows Terminal or in VS Code's terminal, and
> how long a real robot takes from a line to its first motion is unmeasured.

## Known limits

- A goal's own confirm gates are answered by `--yes` alone. Nobody is asked mid-goal.
- `quackd log` replays the verbs and their results and does not yet draw drive's own lines.
- The pilot takes `--llm` alone, without `--base-url`, `--api-key`, `--vision`, `--extra-body`,
  `--price` or `--effort`, and a session keeps no memory.
- A session whose link has failed refuses every call but `stop`, as a run's executor does once
  its heartbeat has failed, and ends any goal it is flying. Quit, and start again once the robot
  answers.
- The fast lane's floors are the stepper's, and no decision LLM has been measured on this
  question.
