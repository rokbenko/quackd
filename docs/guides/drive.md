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
exact    stand (0.153 ms)
result   ok: already standing
exact    forward: move(duration_s=1, vx=0.15) (0.109 ms)
result   ok: walked vx=0.15 vy=0.00 wz=0.00 for 1.0s
exact    turn left: move(duration_s=1.571, vx=0, wz=0.5) (0.184 ms)
result   ok: walked vx=0.00 vy=0.00 wz=0.50 for 1.6s
exact    kick(leg=left) (0.1 ms)
result   ok: kicked with left leg
exact    say(text='good morning') (0.12 ms)
result   ok: said 'good morning' as [chirp]
exact    move(duration_s=0.5, vx=0.1) (0.183 ms)
result   ok: walked vx=0.10 vy=0.00 wz=0.00 for 0.5s
pilot    goal 1 (7.257 ms)
result   success: scripted pilot: nothing more to do
session  quit (0.137 ms)
result   ok: goodbye
record   runs\20261026-034255-drive
```

Each first line names the lane that took the line, what it came to, and the milliseconds from
the moment the line arrived to the moment that lane took it. Here that is quackd's own time,
since no line went to a decision LLM. A question put to you after it, `run it? [y/N]`, is
never counted in it, and the robot's link and the robot itself come after it.

A robot that says where it is can name a spot with `mark_place NAME` and walk back to it with
`go_to_place NAME`, and the Microduck's simulators lay out a room to do it in with `--room`
([guides/places.md](places.md)).

## What a line can be

Every line goes through the lanes in this order, and the first that takes it wins.

1. **The brake.** An empty Enter, a line with no letter or digit in it, or a line whose first
   word is `stop`, `halt`, `brake` or `q`, read up to a hyphen. "quack" is a duck's word and
   never brakes, while "stop now", "Stop." and "stop-now" do. "don't stop" does not, since
   only the first word is read for it. The brake errs on the side of braking: "q-tip" brakes,
   because a false brake costs a line typed again and a missed one can cost a fall. The brake
   waits on nothing:
   - it drops every line that has not started, whether still being worked out, asked about or
     waiting its turn;
   - it ends every verb moving the body, and stops a goal the pilot is flying;
   - it sends the stop to the body directly, ahead of the `stop` verb, which reads the body's
     state first and runs after it for the record.

   A brake is never taken for the answer to a question. An empty Enter while `[y/N]` is showing
   brakes, and the question goes with the line that asked it.
2. **The session's own words.** `help`, `status`, `quit` and `exit`, each a line on its own,
   answered at once. A lone `y`, `yes`, `n` or `no` with no question waiting is told that none
   is, and runs nothing.
3. **The exact lane**, for a line that names a call outright. It knows these forms:
   - every call of the body's that is a choice rather than a value, labelled as quackd's
     decision stepper labels them, such as `kick(leg=left)`;
   - the same call as its name followed by its values in any order, `kick left`;
   - the bare name of a verb with nothing required, which runs it with its own defaults;
   - each alias of those (`walk`, `walk_to`, `get_frame`);
   - the presets below.

   A call written out, as `move(vx=0.2, duration_s=2)`, is read as literals and never
   evaluated: a name it calls, an operator or a starred argument is not a call this lane reads.
   A verb whose one required parameter is a string takes the rest of the line, so `say good
   morning` is a call. The numbers you write are yours, and the executor validates and clamps
   them as it does anybody's.
4. **The fast lane**, only with a decision LLM named, by `--decision-llm` or
   `QUACKD_DECISION_LLM`. A line no verb names, "have a seat", is put to the decision LLM as one
   closed question: which of the body's calls or presets does the person mean, is it something
   for the pilot, or is it unclear? The line you typed goes wherever that LLM runs, a vendor's
   host for a hosted one, with what the robot is doing, the line of a command still running
   among it, and a line about the body. The question is bounded by the stepper's own second and
   held to the stepper's own floors, and to the `.duck`'s confirm gates, as a run's stepper is.
   - A call it is sure enough of runs. One it is less sure of is asked about first, `did you
     mean kick(leg=left)? [y/N]`.
   - `unclear` asks you to say it another way.
   - A line it says is for the pilot goes to the pilot, asked about first where it is less sure.
   - A question nobody could answer hands the line to the pilot: one declined, unreadable,
     failed or out of its second.

   At a pipe every such question is answered no. Nothing moves on a guess.
5. **The pilot**, for anything else. The line is a goal, flown as a whole run of its own on the
   body the session holds. The pilot is the one `--llm` names, or the robot's own where it is
   registered with one, or `QUACKD_LLM`'s, and with `--controller vla` the scripted pilot of
   [a VLA pilot](#a-vla-pilot-a-line-at-a-time). A goal has its own executor, its own verdict and
   its own run directory under the session's `goals/`, and runs inside the session's contract:
   - it gets the body's safe verbs, as `quackd run --goal` gives one, behind any confirm the
     session's `.duck` asks for them, and with a policy server the arm's `manipulate`, behind a
     confirm of its own;
   - its budgets are no more than the session has left, and its steps count toward the
     session's;
   - the session's abort rules are its own;
   - a session started with `--dry-run` sends nothing for a goal either;
   - the session's heartbeat failing ends it;
   - its questions come to you ([below](#a-goals-questions-come-to-you)), and it flies with the
     flags `quackd run` takes for a goal, given once for the session
     ([A goal flies as a run does](#a-goal-flies-as-a-run-does)).

A dangerous verb is never run by drive, however it is named, and not inside another call
either: only a person answering at a terminal in `quackd run` may clear one.

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
you type in drive is that judgement made by you, for that call and the verbs it runs. It runs
without a verdict, and the record says so, as `quackd log` replays the session above:

```
!  gate    verdict: allowed a pipe named this call, which stands for the verdict for it alone
```

Nothing is kept for the calls after it, so a goal's pilot still judges its own task before it
moves anything. Those lines came from a pipe, and a line typed at the keyboard says `a person`
there. Lines read from a pipe or a file clear the gate the same way, and are never recorded as
a person's. A call the fast lane took without asking you is recorded as the decision LLM's
reading of what you typed, and one you said yes to as yours.

A verb that needs a yes before it runs still asks for one. That is a verb of the `confirm`
class, or one a `.duck` lists under `confirm`:

- at the keyboard, you are asked, in the words the record quotes;
- `--yes` answers yes, by its standing rule;
- a pipe without `--yes` is answered no. Drive reads a pipe's lines as commands and never as
  answers, where `quackd run` takes a pipe's next line for one.

Your yes is to the call you were asked about. A verb that call runs inside itself, as
`approach_and` runs its `then`, is answered by `--yes` alone, since drive cannot stop the call
to ask.

On a body that cannot see a fall and cannot get up, an Open Duck on its bridge among them, drive
asks once, before the first thing that moves it, whether you are watching it, as `quackd run`
asks. A no refuses that command, and the next one asks again. `--yes` says you are watching,
and a pipe without it is refused.

## One mover at a time

A command that moves the body ends whatever is moving it, a verb or a goal, and waits for that
to stop before it sends anything, so the stop reaches the body before the new command's first
twist. It waits for a brake still on its way as well. A command still waiting its turn when a
newer one comes is dropped, so only the newest runs. A verb that moves nothing runs alongside:
`observe`, `report state`, `say` and `quack`.

Ctrl-C brakes and the session stays. A second Ctrl-C within 1.5 seconds of the first ends the
session, as `quit`, `exit` and the end of input do. A session ends in order: whatever is still
moving the body is ended rather than waited out, and then the robot is stopped, folded where it
folds, and let go of.

The prompt needs a terminal for its input and for its output. With either piped or redirected,
drive reads lines one at a time, each waited for, as from a pipe.

## The contract

With `--duckfile`, the session drives under that file's contract: its allowlist, read by alias
as the executor reads it, its confirm gates, its abort rules and its budgets. Its body is not
run. A file that leaves its budgets at their defaults, 40 steps and 5 minutes, refuses every
call but `stop` past either.

Without one, the session drives under a contract of its own:

- every verb the body has that is not dangerous;
- the battery rule a goal is held to: once the battery reads below 15%, every call after it is
  refused for the rest of the session but `stop`, which still goes out;
- a task's highest budgets, `max_steps: 1000` and `max_minutes: 180`, and an hour of an arm's
  policy segments, the most `policy.total_s` may be.

It leaves out the rule that ends a goal on the same verb failing three times in a row, since a
person who kicks and misses three times has not asked for the session to end.

## A goal flies as a run does

A goal takes the flags `quackd run` takes for one, given once on drive's command line for every
goal of the session:

| Flag | What it does in a session |
| --- | --- |
| `--base-url`, `--api-key`, `--vision`, `--extra-body`, `--effort`, `--price` | The pilot's model, as for a run, so `--llm local` can name its server. |
| `--max-steps` | Each goal's steps, within what the session has left of its own. |
| `--play` | A goal may give its pilot a part to play. |
| `--memory`, `--memory-dir` | Each goal recalls what earlier runs of the robot learned, keeps one fact for the next, and keeps the places it marks. Off unless given, where `quackd run` keeps it on. |
| `--fov-deg` | The camera's field of view, told to the body once, as the session connects. |
| `--policy-url`, `--policy-token`, `--accept-other-frame`, `--async-chunks` | The arm's policy server, whose `manipulate` a goal may then hand a subtask to. |
| `--controller vla` | Each goal is one instruction to the arm's policy ([below](#a-vla-pilot-a-line-at-a-time)). |
| `--decision-mode`, `--decision-frames` | A goal's stepper, which asks the fast lane's own decision LLM. |

`--decision-llm` on its own is the fast lane and nothing more, as it always was: a goal gets a
stepper only with `--decision-mode` on the command line. drive reads neither
`QUACKD_DECISION_MODE` nor `QUACKD_DECISION_FRAMES`, so a `.env` written for `quackd run` changes
no session.

Each goal reads the session's places, so a place you mark between two goals is there for the
second. What a goal marks or forgets is kept only under `--memory`: without it a goal changes a
copy that lasts as long as the goal, as `quackd run --no-memory` keeps its own places.

### A goal's questions come to you

A goal asks what a run asks at its terminal, and asks you, at the keyboard or in the page: whether
to run a verb its contract gates, whether to go ahead when the pilot is not sure the body can do
the task, and, after a VLA pilot's segment, whether the arm did it. Here the `.duck` gated `move`,
so the scripted patrol asked before each of its three walks, and was answered y, n and n:

```
drive    microduck: type a command, a goal, or help; Enter brakes
ask      goal 1: run walk(vx=0.12, vy=0, wz=0, duration_s=2)? [y/N]
ask      goal 1: run walk(vx=0.12, vy=0, wz=0, duration_s=2)? [y/N]
ask      goal 1: run walk(vx=0.12, vy=0, wz=0, duration_s=2)? [y/N]
pilot    goal 1 (3.778 ms)
result   success: patrol lap complete
```

The goal waits for your answer, and the session goes on reading lines. The keyboard shows one
question at a time, and a `y` or an `n` answers the one on the screen, the last `ask` line. A
goal's question is named after its goal and waits behind any question showing, while a question
about a line you have just typed, a gated call's, is shown at once, since it is the one you are
about to answer. An empty Enter brakes, and a newer line that moves the body ends the goal:
either takes the question back, and the goal's record says nobody answered it, never that you said
no. A line that only reads, `report state` say, runs beside the goal and leaves its question
waiting. In the page the same questions come with their buttons. The goal's own record has each
question and your answer, as `quackd log` replays it:

```
-  asked   confirm: run walk(vx=0.12, vy=0, wz=0, duration_s=2)? -> yes
!  gate    confirm: allowed a human said yes
!  asked   confirm: run walk(vx=0.12, vy=0, wz=0, duration_s=2)? -> no
x  gate    confirm: denied a human said no
```

`--yes` answers yes to the gate and go to the doubt, as it does for a run. A pipe asks nobody: a
gated verb is answered no, and the doubt is left to the pilot.

### A VLA pilot, a line at a time

`--controller vla`, with `--policy-url`, flies each goal with the scripted pilot
`quackd run --controller vla` flies a run with. The line you typed is its one instruction, so it
has to be one: one line of plain text of at most 200 characters, and under a `.duck` that lists
what its policy may be told, one of those, or it is refused before the arm moves. Every goal holds
`manipulate` to that list, a model's as well, and a `.duck` that does not allow `manipulate` at
all is refused before the session starts. You are asked before the segment whether the arm should
try, about the segment itself, and after it whether the arm did it, and only your yes makes the
goal a success.

It needs somebody to ask and something real for them to judge, so it is refused with `--yes`,
with `--dry-run`, through a pipe, and beside a model's flag, `--play`, `--effort` or a goal's
stepper, as a run refuses it:

```
$ printf 'pick up the red cube\nquit\n' | quackd --no-color drive --robot lerobot:mock --controller vla --policy-url http://127.0.0.1:8080
x error: --controller vla asks a person whether the arm did the task, and there
is no terminal to ask on
  run it from a terminal, not through a pipe or a script
```

`--decision-llm` alone is let through, as the fast lane. The seconds the policy drives the arm are
the session's: each goal is held to what the session has left, under its own total, the
`.duck`'s or else two minutes, and a session without a `.duck` has an hour of them, as it has a
task's highest budgets.

## The record

Each session is a run directory of its own, `runs/<stamp>-drive[-name]/`. Its `transcript.jsonl`
opens with a `run_start` once the robot has connected and closes with a `run_end`, whose
outcome says how the session ended, so `quackd log` replays it. Between them it holds these
lines:

- `command` for each line typed or read as a command, and who sent it;
- `routed` for the lane that took it, with its milliseconds;
- `fast` for each question the fast lane put, with its confidence, its floor and its gate;
- `started` for each call as it goes to the executor, whose gates may still refuse it, and
  `ended` for each line once what it came to is over;
- `brake` for every brake;
- `goal_start` and `goal_end` for each goal;
- `prompt` for a question about watching a body that cannot see a fall;
- the executor's own records of every verb, gate and intent.

An answer to a question is no `command`: it is in the row of the call it answered, and a goal's
questions and your answers are in that goal's own record, with `channel: web` where the page
asked. A session whose arm has a policy server names it in `run_start`, as a run does.

Its `summary.json` has a `drive` block, this one from the session above:

```json
{
  "lanes": {"exact": 6, "pilot": 1, "session": 1},
  "outcomes": {"ok": 7, "success": 1},
  "to_start_ms": {
    "exact": {"n": 6, "p50": 0.2, "p90": 0.3, "max": 0.3},
    "pilot": {"n": 1, "p50": 7.3, "p90": 7.3, "max": 7.3}
  },
  "to_motion_ms": {"exact": {"n": 4, "p50": 0.5, "p90": 0.7, "max": 0.7}},
  "brake_ms": {"n": 0}
}
```

All figures are milliseconds:

- `to_start_ms` runs from a line to its call going to the executor, by lane;
- `to_motion_ms` runs to the first intent other than a stop that the body accepted for a call
  the session ran itself, by lane, since a goal's motion is in the goal's own record;
- `brake_ms` runs from a brake to the stop it sent, over every brake.

Each goal the pilot flew is under `goals/NN-what-you-typed/`, a whole run `quackd log` replays.

> [!WARNING]
> **Nothing here has driven a real robot yet.** Drive has run against the mock Microduck and
> the scripted pilot, and its keyboard against prompt_toolkit's own pipe input. Its prompt has
> not been tried in PowerShell under conhost, in Windows Terminal or in VS Code's terminal, and
> how long a real robot takes from a line to its first motion is unmeasured. A VLA pilot's
> segments have run on the LeRobot mock's scripted policy alone.

## Known limits

- `quackd log` replays the verbs and their results and does not yet draw drive's own lines.
- A question a goal puts from inside a composite, a gated `kick` as `approach_and`'s `then`, waits
  inside the composite's own time, 90 seconds for `approach_and`, and is taken back when that runs
  out, recorded as unanswered.
- A session whose link has failed refuses every call but `stop`, as a run's executor does once
  its heartbeat has failed, and ends any goal it is flying. Quit, and start again once the robot
  answers.
- The fast lane's floors are the stepper's, and no decision LLM has been measured on this
  question.
