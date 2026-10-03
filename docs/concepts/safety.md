# Safety

A biped falls in 0.3 s; an LLM answers in 3 s. Everything here follows from that.

## Layers

| Layer | Owner | What it guarantees |
|---|---|---|
| Body | the robot's own controller | **Whatever that particular body actually offers, which is not the same everywhere.** The Microduck's `robotd` gives joint and thermal clamps, fall detection and a **deadman**: velocity goes to zero when `robot.move` notifications stop. An Open Duck Mini v2 gives *none of those*: its deadman is quackd's own daemon on the Pi and the human watching is its only fall detector (details under "On hardware"). The body is still the sole safety authority: clients send intents, never motor writes. What each body offers is declared in its manifest's `safety_authority`, and `quackd doctor` prints what the robot itself reported (see "On other bodies"). |
| Judgement | the pilot, held to it by quackd `Executor` | Nothing that moves the body runs until the model has said, against the robot's datasheet, whether the task fits the body at all. It is the model's own judgement; what the executor guarantees is that it was made, recorded, and made *before* the first leg moved. A **coordinator** flock's member is a state machine with no pilot to ask, so it is never asked: a role's `needs` against a datasheet is what that kind of flock has instead. A **pilot** flock's member is a whole pilot and is asked exactly as a solo run is, about its own part of the task rather than all of it ([flock.md](flock.md)). |
| Conversation | quackd `Executor` | The LLM and MCP clients can only do what the `.duck` allows, as often as the budget allows, with a human in the loop where the contract says so. |
| Session | quackd `Heartbeat` + `KillSwitch` | A dead transport or a worried human ends in a `stop` intent. In a flock one kill switch reaches every member's executor, so Ctrl-C stops every body rather than the one in front. |

> [!NOTE]
> **The optional discrete stepper changes none of this.** With `--decision-mode on`
> ([decision-llms.md](decision-llms.md)) some turns are answered by a decision LLM instead of
> the model, whichever one `--decision-llm` named and wherever it runs, and every
> one of those calls goes through the same `Executor.run_verb` as every other, so the
> allowlist, the budgets, the confirm gates, the preconditions and the body's own safety
> authority bind it exactly as they bind the model. Two things about it are structural rather
> than enforced, which is stronger: it can never author a number, because a verb with a free
> number in its schema is never offered to it at all, and it can never author a sentence, so
> it cannot record a feasibility verdict, cannot declare an outcome, and cannot end a run.
> The judgement layer above is untouched: nothing that moves the body runs until the *model*
> has said whether the task fits it.

## The executor (mirrors upstream's own rules)

Every verb call — from the agent loop, an MCP session or the discrete stepper — passes
`Executor.run_verb`, which
applies these in order and stops at the first one that refuses. The **gate** column is the word
the log and the transcript print, so a refusal tells you which row you are on.

| Gate | What it checks | What you change |
|---|---|---|
| `abort` | the run has been aborted. `stop` is exempt, so the brake still works | nothing: the run is over |
| `allowlist` | the verb is in `verbs.allow`. `stop` is always allowed | the `.duck`'s `verbs.allow` |
| `unknown` | quackd has never heard of this verb | the spelling, or the robot (`quackd list-verbs`) |
| `verdict` | the pilot has judged the task feasible against the datasheet. `stop`, `observe`, `report_state`, `say`, `quack`, `express`, `gaze`, `look` and `introspect` run before it, and so does any verb its adapter declared `read_only`, because a pilot has to look at a thing before judging whether it can lift it | nothing: the model calls `assess_task` |
| `params` | the arguments fit the verb's schema | the call. This is feedback to the model, not a crash |
| `confirm` | `verbs.confirm`, or a `safety_class` of `confirm` or `dangerous`. y/N in the terminal; over MCP it refuses unless `--yes` | answer y, or pass `--yes` |
| `segment` | one segment of a body's learned policy at a time: a `pick` or a `manipulate` sent while another one's segment runs is refused rather than queued. Over MCP every call is a task of its own, and without this two calls could each be checked against seconds not yet charged | wait for the segment to end, or send `stop` |
| `budget` | `max_steps` and `max_minutes`. These cap an MCP session too, which has no loop of its own; `max_llm_calls` is the loop's. A `pick` or a `manipulate` is also checked against the seconds of policy segments the task allows (`policy.total_s`, 120 s unless a `duck: 3` file says), each segment charged the seconds it ran however it ended, so the last may overrun by its own length and no more | the `.duck`'s `budgets` and `policy` section, or `--max-steps` |
| `abort_when` | the battery threshold, and consecutive failures once the result is in | the `.duck`'s `abort_when`, or the robot |
| `precondition` | what the manifest says this verb needs: not fallen, not sitting, torque on | the robot's state |
| `dry_run` | `--dry-run` is on, so nothing is sent | drop `--dry-run` |
| `cancelled` | the caller went away mid-verb: an MCP client dropped the request, or a second Ctrl-C | nothing: a `stop` went out |

Execution races a **timeout** against the abort, so a kill switch cancels the verb that is
running. A verb that times out or raises stops the robot and comes back as a failed result
rather than an abort.

The `cancelled` row is the one worth knowing about: that path used to return at once and leave
the legs moving with nothing to halt them, which is the failure this page exists to rule out.

## Who the record says was asked

The `confirm` row above records what the gate decided. The run also records the exchange
itself: a `prompt` event carrying `what` (`confirm`, `decide`, `acknowledge`, `hand_off`,
`release` or `judge`), the question in the words it was put in, and the answer. Those six are
the confirm gate, the pilot's own question when it is unsure (below), the acknowledgement that
somebody is watching a fall-blind robot, each half of a `--by-hand` handover, the offer to
release an arm whose rest move missed (below), and the question a `--controller vla` run ends
on, whether the arm did the task (below). What an answer *caused* was always written down by
whoever acted on it, in `gate.answer` here, `assess.human` for a verdict and the `hand_off`
and `release` stages for an arm. What was asked, and what somebody said back, was not.

**It is written only where a person was really asked.** `--yes`, a flock's standing answer and
the MCP server all answer without asking anybody, and none of them leaves a `prompt` event: the
server has no terminal to ask on, so it refuses a gated verb unless it was started with `--yes`
and clears it standing if it was. Neither does a run with no terminal under it.
`yes | quackd run` and `quackd run < answers.txt` open the gate exactly as they always have,
because `input()` reads a pipe as happily as a person and opening it is what the pipe asked
for. What is withheld is the testimony: there is no `prompt` event, and the gate's own reason
reads `the confirm gate was allowed` where a run somebody stood through reads
`a human said yes`.

That distinction is a safety property and not a nicety. A run directory is what somebody
reaches for after a robot did something it should not have, and the question they put to it is
who cleared the verb that did it. A record that invents a witness is worse than no record,
because it sends that reader after a person who was never there, and it does so at the one gate
whose whole job is to put a human in front of a verb. It fails the other way on purpose: an
asker that reaches a person and is not marked as one writes nothing, so the record under-claims
rather than inventing somebody.

The verdict's own record is held to the same rule. `assess.human` is the answer the run was
given, `go` or `no_go`, which is what the gate reads, whoever gave it. Beside it
`assess.answered_by` says who gave it: `a person` only where a `prompt` event would say so too,
and otherwise `--yes`, `a flock's standing answer`, `a pipe`, or `a standing answer` for anything
else that answers without asking. The line the terminal and `quackd log` draw from it names the
same, `(--yes said go)` where a run was started with `--yes`, and a run recorded before
`answered_by` existed is drawn as `(answered go, and the record does not say by whom)`. A pilot
whose doubt was answered no ends the run with the same name in its reason. A prompt that raised,
on the end of its input or with click's `Abort`, answered nothing: the gate reads it as no, and
the event records what it raised as `raised` in place of an `answered_by`, drawn as
`(the question went unanswered: the prompt raised EOFError)`, and no `prompt` event says
anybody answered.

The questions and the answers are in `terminal.txt` too, and that file is the only place the
answer survives in the words it was typed in. Neither half of the exchange goes through the
view that keeps the file: the question is written out raw and the terminal itself echoes what
you type, so both are put into `terminal.txt` deliberately rather than picked up off the
screen. The record keeps a yes or a no; the file keeps what was typed.

## Heartbeat

A task pings `transport.heartbeat()` every 500 ms (`robot.health` on a Microduck, each
backend's own health call elsewhere, a liveness check in sim). One failure → `stop` intent → abort flag → the loop ends with
`outcome: aborted`. Upstream's own rationale: "LLMs stall mid-inference".

## A detector on another machine

With `--host`, the detections can come from YOLO on a Jetson rather than from this process
([jetson.md](jetson.md)), and a board can stop answering in the middle of a run. That is never
a reason to change detector. A call that fails gives that frame no detections, and the run
keeps the detector it started with. The colour detector does not label the same things on a
real camera, so a quiet switch would change what `go_to` steers at with nothing in the record
saying so, and the header would name a detector the run had stopped using.

A frame the board could not read is not taken for an empty room. `go_to` and `search_scan` stop
the body on the first one and fail with the board's reason, so neither turns the body blind,
and the pilot is told the detector failed rather than that the target is gone. `observe`
reports nothing seen with the board's reason beside it, and a `quackd run` writes a `note` to
its log when the board's detector stops answering and another when it answers again. A board
that answers slowly stops the body too. While `go_to` waits for a frame it re-sends its last
twist for 0.3 s and then sends a zero one, so a body with no deadman of its own does not drive
on in the meantime: a rosbridge base has none, and a ToddlerBot's is fed by its adapter's
keepalive for as long as quackd is connected.

The board's camera never takes over a body's own view. Its frame is the primary view only for
a body with no camera of its own, because a body's bearings are calibrated for its own lens and
a board on a bench says nothing about which way the robot faces. A snapshot that fails or goes
stale costs that picture and never the run.

## Kill switch

Ctrl-C and `q` (when stdin is a terminal) set the same abort flag; the loop's `finally`
always sends `stop`, puts a body that has a recorded rest pose back into it, and closes the
transport. Works on Windows (signal handler, not `loop.add_signal_handler`).

A second Ctrl-C raises straight through that teardown. If it lands during the rest move the
close is skipped, and a LeRobot arm is left holding rather than sagging. LeRobot can still
disconnect the arm as the process lets go of it, but quackd builds the arm asking that
disconnect to keep torque, and asks for the release itself only where it may let go. That is
the safe direction, and it is written up under "A LeRobot arm" below because it is not a tidy
exit. In 0.14 and before the arm was built asking that disconnect for the release, so it could
fall instead.

There is one window where that is not what happens. A `--by-hand` run ends by asking whether
to open the gripper before the arm folds up, and a second press landing on *that* wait is
caught rather than let through, because it means "skip the question" and not "abandon the arm
energised with no record written". The gripper is left exactly as the run left it, the arm is
still folded to its recorded rest pose, the transport is still closed, and the record still
ends with a `run_end` and a summary. It buys one press and no more: the guard is on that wait
alone, so a third press lands somewhere without one and quits at once, on the terms of the
paragraph above.

**SIGTERM is not one of them.** quackd installs a handler for SIGINT and for nothing else, so a
bare `kill` and a systemd unit's default stop both end a run without the `finally` above ever
running, and the process dies where it is. Neither sends the robot a `stop`, so anything you
wrap `quackd run` in should send SIGINT instead. The Open Duck Mini's bridge and the ToddlerBot
daemon are the other way round and settle on both, which is what a daemon has to do: one is
started by a unit this repository ships, and the other is started by hand and stopped by
somebody typing `kill`.

## When the pilot is unsure

`assess_task` has a third answer. `uncertain` means the verdict itself turns on a figure the
pilot cannot judge from where it is: the mass or size of a thing that decides a limit, or a
limit the maker never published. Not having found the target is not by itself one of those,
because finding the thing is the task. It becomes one when a limit turns on that unseen
thing's mass or size, which is why the verbs that look run before the verdict: the pilot can
take a frame from where it stands. What it cannot do is go to the thing first. `search_scan`,
`go_to` and `move` all wait for the verdict, so when the deciding figure is on something out
of frame, `uncertain` is the honest answer and the person is the way past it. At a terminal
that is a y/N question with no as the default, and one a person really answered is in the record
as a `decide` prompt ([above](#who-the-record-says-was-asked)).
A no ends the run `aborted` and exits 1, not `infeasible` and 3, because a person
stopping a robot is the kill switch's kind of act rather than a statement about the body.

A go is told to the pilot as a go: its doubt is recorded, a person read it and said go, the
verbs that move the body now run, and it should not assess again on the same doubt, only on
something new it sees. Up to 0.13.0 it heard only that motion now ran, which reads the same
as its own `feasible`, and the prompt invites a pilot to assess again when it changes its mind.
On the 2026-09-23 bench a pilot a person had just cleared assessed the same doubt again, as
`infeasible`, and the run ended on a question somebody had already answered. Over MCP nobody
sets that answer: the model asks you in the chat and records its own verdict again.

**`--yes` answers that question with go**, the same way it answers a confirm gate, and
`quackd record` always passes it. So a `--yes` in a script no longer only skips
confirmations: it also clears the pilot's own doubt about whether the task suits the body.
Its pilot, like one cleared by a flock's standing answer or a pipe, is not told a person read
the doubt, because nobody did: it hears that the run was started to go ahead without asking
anybody, and, as after a person's go, that the verbs that move the body now run and it should
not assess the same doubt again.
Over MCP there is no terminal and nothing clears it, so the verdict stays pending and the
model is told to ask the person it is chatting with ([mcp.md](mcp.md)).

## A pilot that cannot judge: `--controller vla`

`quackd run --controller vla` flies a LeRobot arm with a scripted pilot that hands its learned
policy one instruction at a time ([adapters/lerobot.md](adapters/lerobot.md#a-scripted-pilot-that-a-person-judges---controller-vla)).
Neither the script nor the policy can tell whether the task was done, so a person is its whole
verdict, twice. Its answer to `assess_task` is always `uncertain`, so the question above is put
before anything moves. After the last segment it asks whether the arm did it, and the run is a
success only on a yes from a person really asked: a `judge` prompt in the record. A no, a pipe
on stdin, or nobody there is a failure. The loop holds any pilot that says it cannot judge to
that rule, whatever the pilot declares. A budget that ends the list early still asks about the
segments that ran, and the run ends on its budget either way.

The seconds you take to answer are yours and not the run's: they come off `max_minutes`, the
way a `--by-hand` handover's do, so a long look at the arm never turns your yes into a spent
budget. A prompt that ends without an answer, at the end of its input or with click's `Abort`,
is nobody answering and not a no. The run fails, no `judge` row is written, and the reason says
what the prompt raised. While it waits, as while the confirm gate and the verdict's question
wait, the run's heartbeat does not beat, since all three are asked on the thread the heartbeat
runs on. Nothing is driving the arm then: its last segment has ended, and the arm holds where
that segment left it, as a servo holds the last goal it was sent.

So a vla run is refused, before anything connects, wherever nobody could answer: with `--yes`,
with no terminal under it, and over MCP, where `serve-mcp` refuses `--controller` in words. It
is refused with `--dry-run` too, which moves nothing for anybody to judge.

## When a feasible verdict contradicts itself

A verdict says two things: whether the body can do the task, and, in `needs`, what the task
would require of a body. They can disagree. A pilot on a Microduck answered `feasible` to a 45
minute patrol and wrote `endurance_min: 45` in the same record, on a body whose endurance
nobody has ever published, and the duck walked until its step budget ran out.

So a `feasible` is now held to the body's own datasheet before it is recorded, by the same
function that holds another robot's bid at the coordinator. A need the sheet does not meet, or
does not publish, is refused and named, and the pilot is told the three ways on: `infeasible`
if that need decides the task, `uncertain` if a person could know the figure, or a corrected
need if it asked for more than the task turns on. An `uncertain` and an `infeasible` are left
alone, because one asks a person and the other ends the run anyway.

There are four exceptions, each of which would otherwise refuse an honest answer, because the
check and the words the pilot reads have to agree:

- **A zero asks for nothing**, for every number, `work_height_m` included. The tool tells the
  pilot to fill `needs` in even for a feasible verdict, and to give `0` for what the task does
  not turn on.
- **`none` asks for nothing**, for `mobility` and `manipulator`. It is how a pilot on an arm
  bolted to a table says the task goes nowhere. `any` still means some kind, so the arm fails
  `mobility: any`.
- **An unpublished terrain meets `indoor_flat`** on a body that moves, because that is what
  the prompt tells such a body to assume about itself.
- **A body that does not move meets `indoor_flat`**, and anything above it is refused as
  `(it does not move)`, which is the sentence its prompt carries instead of a terrain.

A body with no datasheet at all gets neither floor: its prompt tells it to treat every
physical limit as not published. And one reading is kinder to a pilot's own sheet than to a
stranger's: a `work_height_m` against a sheet with no working height band is not held against
the pilot's own verdict, because the prompt never lists a working height as missing, while the
coordinator, a flock role and the list of bodies that could still refuse it.

Up to 0.13.0 there were two, the zero stopped short of `work_height_m`, and the only
floor was the one for a body that moves. On the 2026-09-23 bench the pilot of an SO-101, filling
every field in as it was told, was refused by its own sheet for needs of exactly that kind on
nearly every run, and each refusal became an `uncertain` and then the y/N question at the
terminal.

On the one model measured so far it turns a silent `feasible` into an `uncertain`, which is
then the question above, so under `--yes` the check costs one LLM call and leaves the
contradiction in the transcript rather than stopping the run.

Two things it cannot do. **It cannot catch silence**, because it reads what the pilot
declared: a pilot that never mentions the figure its plan hinges on passes exactly as it did
before. And **it asks more of a pilot that answers fully**, which is the same fact from the
other side. A duck asked to nudge a 60 g ball has no published payload to compare against, so
a pilot that honestly writes `payload_kg: 0.06` is refused where one that writes nothing is
not. That is refuse by default doing what [ADR-0032](adr/0032-datasheets-and-the-verdict.md)
says it should, and the way to answer it once rather than every run is a `duck: 2`
`datasheet:` block in the task file: a figure given there replaces the adapter's and is
rendered as coming from you, so the pilot is comparing against a number somebody stands
behind.

## Dry run

`--dry-run` sends nothing, and the log names every verb a model *would* have run, with the
parameters it chose:

```
⚠  gate    dry_run: skipped would run walk_to, sent nothing (target='ball', stop_distance=0.22, timeout_s=20)
```

A parameter the model left unset shows as `null` rather than being dropped, because on a dry
run the omission is the thing you are checking. The verbs whose adapter declared them
`read_only` still run: `observe` (alias `get_frame`), `report_state`, the rosbridge base's
`introspect`, and whatever a body quackd never shipped flags for itself. Use it the first
time you point a new `.duck` at hardware.

It does not get you past the verdict, because that gate runs before this one. A task the
pilot judges infeasible ends with nothing logged rather than with the list of verbs it would
have sent, which is the one case where `--dry-run` tells you less than you asked for.

A dry run does not move the arm into its rest pose either, because it moves nothing. On a
LeRobot arm with a pose recorded that is a change of behaviour worth knowing before you press
Ctrl-C and walk away: the arm ends the dry run holding itself up, with a line saying so,
instead of going limp.

## Rehearsing on the arm's simulator

`lerobot:mujoco` runs the arm's real backend over a physics model, and it never touches
hardware. It imports no LeRobot and opens no port: its follower and its cameras are simulated,
its address is a calibration file read as a regular file, and an address shaped like a serial
port, `COM5` or `/dev/ttyACM0`, is refused on its shape before anything looks at it, because on
Windows a port answers to its name in any directory. `quackd preflight`, which drives a robot
through task file after task file and seed after seed with nobody watching, refuses any robot
that is not a simulator before building it, so a typo cannot send a rehearsal to the arm
([adapters/lerobot.md](adapters/lerobot.md#rehearsing-a-task-file-quackd-preflight)).
`quackd doctor` and `quackd robot release` say it is the simulator rather than asking you to
support or hold an arm that is not there.

A rehearsal that passes is not a safety claim about the arm. The simulated servos do what the
arm's do with a goal, a step cap and a clamp, but the dynamics are the model's and the joint
signs, zeros and camera positions are assumed, so a task that holds on the simulator can still
drop, stall or collide on the desk. Rehearse at home, then `--dry-run` at the bench, then run it
with a hand near the switch.

## On hardware

One of the seven bodies has run on hardware, on two afternoons. On 2026-09-15 a LeRobot SO-101
follower arm ran `lerobot-lookout` and then a series of free-form `--goal` runs, piloted by
OpenAI gpt-6-astra on Windows 11 with lerobot 0.6.1: it waved the wrist about 27 degrees either
way, waved again from a raised pose, and opened and closed the gripper. **It also fell at the
end of every one of those runs**, because LeRobot's `disconnect()` drops torque, which is the
whole reason the rest pose below exists. On 2026-09-23 the same arm ran 26 runs on quackd
0.12.0, under a registered name with a rest pose recorded, and that afternoon is where the
parking, release and verdict changes on this page come from. None of those changes has run on an
arm yet. [lerobot-first-run.md](lerobot-first-run.md) is the account, including what the first
day did not measure, and most of what this page would want to know is on that list: whether the
holding band is right, what a joint reads after ten minutes of work, and whether a stall is
caught on purpose rather than by luck. The other six bodies have not been on hardware at all.

If the body is a Microduck, run the contract in the physics simulator first
(`--robot microduck:mujoco`): it is the only place quackd can show you a body that undershoots,
refuses and falls over, and nothing there can be hurt. If it is the LeRobot arm, rehearse it on
the arm's simulator first (`quackd robot twin`, then `quackd preflight`), which runs the code
that will drive the arm and says nothing about how the arm will move. When you do reach the
robot, start with `--dry-run` every time, then a `.duck` whose `allow` list is the smallest thing that could work,
then widen it. **You are responsible for your robot.**

**There are two places where quackd takes torque off a robot, and only a person can ask for
either.** The first is `quackd run --by-hand`, which releases a LeRobot arm at the start of a
run so you can lift it, put whatever the task needs into the gripper, and set the pose the run
begins from. Every word of that is a limit. Only that arm: the flag is refused on the other six
bodies by name. Only at the pose recorded with `quackd robot rest-pose`, because an arm held up
by torque alone falls the instant torque goes, so the arm refuses to be let go of anywhere else
and a run on an arm with no pose recorded is refused before it starts. Only when a person typed
the flag at a terminal, which is also checked before anything moves. And never by anything else:
releasing is not a verb, it is in no `allow` list, and it is deliberately not on the
`RobotAdapter` protocol, so there is nothing for a model or an MCP client to call. When you
press Enter, quackd writes the pose you left the arm in as the goal *before* it re-enables
torque, writes it again, reads it back, and ends the run rather than starting it if a joint
moved more than five degrees while your hand was still on it.

The second is for an arm left holding itself up, and it releases the arm wherever it stands,
because the person asking is holding it. `quackd robot release NAME` says that connecting takes
torque off for a moment and that the release lets the arm fall, asks, and only then connects,
releases, reads torque back off every motor and says what it read. And a run whose rest move
missed, with you at its terminal, offers the same thing before it closes: hold the arm and press
Enter, or leave it for 60 seconds and torque stays on as it always did. The limits are the first
door's, less the pose. Only the arm. Only when a person asks: the command asks at a terminal
unless `--yes` says you are already holding the arm, and refuses with neither, and the offer is
never made on a dry run, over MCP or in a flock. And never a verb or a tool. On 2026-09-23 the
power switch was the only other way to put down an arm a run had left energised, and every run
that got to its end that afternoon finished there
([adapters/lerobot.md](adapters/lerobot.md#the-torque-rule)).

**If a run ends while the arm is still limp in your hands and no take-hold has been refused,
quackd picks it up before it folds it.** That state takes a Ctrl-C during the placement wait or
a heartbeat that died there, and the way out of it is the `stop` every teardown opens with: on
an arm that is in somebody's hand that stop takes hold first, at wherever your hand has it, and
the rest move then puts it down from there. Sending a goal to a limp servo would have been a
stop that stopped nothing. It never takes while a joint reads outside its calibrated travel,
because a goal written where that joint is lies past the travel and is pulled to the end of it,
and none leaves the servo the last goal it had, so torque stays off, and you are told at once,
as the take-hold at Enter tells you, which joint and where it reads, and it goes on the record.
Once a take-hold has been refused, at the end of the placement wait or in that stop, quackd
leaves the arm alone for the rest of the run: no second take-hold, even after you move the joint
back inside, no goal and no fold, because the arm is in your hands. What you are told is what a
read found. An arm still lying at its rest pose with every motor off, because you pressed Enter
without lifting it out of a fold recorded past its travel, is said to be still limp at its rest
pose, with the joint to lift inside its travel before quackd takes hold. A take-hold whose read
found some motors on names the joints that hold and says the rest is limp, and to keep hold of
the arm and cut its power. One that asked for torque with nothing read back may have left the
arm energised, so you are told quackd cannot confirm whether it has torque, to hold it as though
it may move or drop, and to cut its power to be sure, and the close says the same. Where the
take-hold switched nothing on, the close says so in the one line that is worth reading:

```
the arm is limp and in your hands (...): put it down before you let go of it, because nothing is holding it up
```

That line and the "torque was left on" one under "A LeRobot arm" below are opposites on purpose,
and quackd has to pick the right one. Whoever reads this one is holding the arm, and being told
instead that it is holding itself up is the sentence that gets an arm dropped. Where a release
left some motors energised, "nothing is holding it up" would be the wrong one too, so that close
names the joints that still read torque on and ends on cutting the power. And where nothing read
the release back at all, a release that raised part way or that a Ctrl-C landed on, the close
says exactly that, since the motors after the one a release stopped at keep their torque: hold
the arm as though nothing holds it, put it down, and cut its power to be sure. A read speaks for
the arm only when the bus carried it after the last torque write, release or take-hold, because
the run's heartbeat reads the arm on its own clock, and a read that got the bus just before a
write says nothing about what that write did.

**An XLeRobot (a 12 kg dual-arm cart):**

- **The watchdog stops the wheels and nothing else.** Upstream's 500 ms deadman calls
  `stop_base()`, so the fourteen arm and head servos keep holding their last goal under
  torque. `deadman_scope` says `base_only`, and that is the robot's entire safety authority.
- **Nothing reports a battery**, so a battery abort can never fire. The power station's
  switch is the only e-stop and it is not on the network.
- **The host exits by itself after an hour** with no supervisor anywhere upstream, so a long
  session ends as a heartbeat failure rather than an error.
- Blocks under the wheels until you have checked the turn direction: quackd converts rad/s to
  the deg/s the wire wants, and a wrong conversion is a 57x error.
- Work through [xlerobot-hardware-checklist.md](xlerobot-hardware-checklist.md).

**An AlohaMini (two arms on a 600 mm motorised lift):**

- **As upstream ships it the arms are limp**, so the safest bring-up is on the stock host,
  where the base and the lift can be exercised with no arm risk. quackd's own host wrapper is
  what turns torque on, and upstream's `disconnect()` turns it off again, so a loaded arm
  falls when that host exits.
- **The watchdog covers the base and the lift, never the arms** (`base_and_lift_only`).
- **`home()` leaves the lift travelling** at full speed, because the write that would zero
  that register is commented out upstream. quackd sends `stop` as its first command after
  connecting for exactly this reason.
- Clear the lift's whole travel before powering it. How fast it moves in mm/s is not stated
  anywhere upstream, so quackd's duration estimate for `lift` is an assumption.
- Work through [alohamini-hardware-checklist.md](alohamini-hardware-checklist.md).

**A ToddlerBot (a 56 cm, 3 kg humanoid):**

- **It cannot get up.** There is no get-up policy for this body at the pinned commit, so
  a fall ends the run and needs a human. Every moving verb refuses once it is down.
- **Work through [toddlerbot-hardware-checklist.md](toddlerbot-hardware-checklist.md) in
  order.** It keeps the feet off the ground until step 13, and steps 11 and 12 (pull the
  network cable mid-move, then send `SIGTERM`) are the two that matter most.
- **The deadman is a slew, not a stop.** There is no velocity at this hardware boundary:
  the command is an absolute pose. On silence the daemon quackd ships slews to the safe
  pose at upstream's own rate, waist first, and holds. It never goes limp, because on
  this body torque off is a fall.
- **quackd owns the control loop here**, which is true of no other body. Upstream's own
  `step()` is a no-op, so nothing times out and nothing re-arms without the daemon.
- **A model server or a detector on the same board competes with that loop.** This robot
  carries a Jetson, and quackd stays on your laptop, but the board can hold a model server and
  quackd's host daemon beside quackd's ToddlerBot daemon ([jetson.md](jetson.md)). A model
  server or a YOLO detector saturating the CPU and the memory bus is the load that starves a
  fifty hertz loop, and here a starved loop is a fall. Nobody has measured that contention on
  any board: keep the robot on a stand the first time, watch `tegrastats` while a model answers
  and the detector runs, and consider pinning the model server off the cores the loop runs on.
  The host daemon's unit already yields, at `Nice=10`, under `MemoryMax=2G` and first in line
  for the OOM killer.
- A good first contract is the shipped `toddlerbot-lookout`: it moves no leg, no arm and
  no waist.

**A Microduck (a 25 cm biped):**

- **Run on the floor, not a table.** A 25 cm biped and a table edge do not mix.
- **Keep pets and kids clear of `kick`** (and `grab`, and `roulade`).
- **The gamepad preempts remote control.** Upstream arbitrates authority; there is no
  stop button because releasing the sticks stops the robot via the deadman. quackd does not
  try to out-rank the pad.
- quackd never sends `robot.relax` (torque off — the robot collapses) or `robot.init`
  (moves every joint). Use `robotctl` for those, with the robot on its stand.
- A good first contract: `allow: [quack, gaze, stop]`, then add walking.

**A LeRobot arm (an SO-101 class arm on a desk):**

- An arm sweeps a volume. Clear it before `move_joints`, and keep hands out of the path.
  A gripper is a pinch hazard even at the 50 % torque cap LeRobot writes at `configure()`.
- **That cap is on the gripper and on nothing else**, so the five body joints have no
  protection but their own firmware and quackd's heat gate: a joint at or above 60 °C refuses
  to move.
- **There is no e-stop and quackd cannot give it one.** Cutting the servo supply is the only
  thing that stops this arm in every case, the one where quackd itself has died included.
- **A slow move is a long one.** `move_joints` takes the `duration_s` it is given, up to 12
  seconds, a nudge of a few degrees included (only a goal the joint is already within a tenth
  of a degree of goes out at once), and a joint that meets something partway is called stalled
  only when that time is up, pushing against a goal one step ahead of it until then. A goal
  outside the travel the pilot was shown is refused before anything moves. Nothing moves faster
  than the step cap, 50 degrees a second, whatever the time asked for; a joint that reads past
  its travel first rises to the edge of it at the servo's own speed, before any pacing starts.
  Keep the hand near the switch for the whole of a slow move, not only its start
  ([adapters/lerobot.md](adapters/lerobot.md#the-manifest)).
- **`pick` and `manipulate` hand the whole arm to a learned policy** for one segment, `pick`
  for up to a minute and `manipulate` for 10 s unless a task file says otherwise, up to 60. Both
  are confirm-gated for that reason, a `--goal` run's `manipulate` included, so a person at a
  terminal is asked before each segment unless `--yes`, or a pipe or file on stdin, answers for
  them. **Under `--yes`, and under `yes | quackd run` or `quackd run < answers.txt`, nobody is
  asked:** a segment the pilot calls goes ahead on the pilot's word and a yes nobody said, and
  the record says the gate was allowed without naming anybody
  ([above](#who-the-record-says-was-asked)). Over MCP both verbs need `serve-mcp --yes`, so
  quackd asks nobody before any segment there, and `quackd preflight` clears every segment of a
  rehearsal the same way, on the simulator alone. `--controller vla` refuses `--yes`, and a run
  with no terminal to ask on, since its verdict is a person's. Watch every segment, and keep
  `stop` within reach: a stop from anywhere, the heartbeat's included, ends a segment before it
  holds the arm.
- **A policy moves no joint faster than a verb may.** Its step cap is the verbs' speed, 50
  degrees a second at the default step, divided by the policy's rate, so a policy at 30 Hz takes
  steps of about 1.7 degrees and never more than one verb step. The cap is on the follower for
  the length of a segment and put back to the verbs' step however the segment ends. A goal past
  the travel is clipped to it and counted, and one held there for a second ends the segment. So
  does a hot joint, torque off, a camera that gave no frame, an action that is not a finite
  number, three failed sends in a row, a read the arm did not answer, and a policy that stops
  answering, each with the arm held.
- **The stretch past the travel is the one a policy's cap does not cover.** A joint that reads
  past its travel is left out of every goal a policy sends, as a stop leaves it out, and a
  segment will not start with one more than 2 degrees outside. That keeps a policy from starting
  a rise out of a fold. It cannot halt one a move had already started, which the servo finishes
  at its own speed, so the power switch is still the only stop for that stretch.
- **The policy runs in a server of its own**, `quackd policy serve`, and no quackd command loads
  a checkpoint in the process that holds the serial bus, because a checkpoint's processors can
  name code to import. `load_policy()` in the arm's backend, an older Python helper that nothing
  in quackd calls, still would, with none of the server's checks and no check at connect that
  what it built fits the arm (the `LOAD_POLICY` row in
  [adapters/lerobot.md](adapters/lerobot.md#the-policies-upstream-lerobot-061)). The server's
  answers move the arm, so it wants a token on every request and binds loopback unless a TLS
  proxy stands in front of it, and the arm checks at connect, before any torque, that the policy
  fits this arm's motors, cameras and calibrated travel, and that the latency its server
  declares is one its chunks can carry ([policies.md](policies.md),
  [SECURITY.md](../SECURITY.md)). A server that stops answering ends the segment with the arm
  held, starved on the arm and on the client's own deadline on the simulator. What no bench has
  measured yet is how fast its loop runs on the real bus while the same laptop infers.
- **`--accept-other-frame` changes what drives the arm, never where it may go.** Given beside
  `--policy-url`, it lets a policy connect whose learned readings lie outside this arm's
  calibrated travel, one trained on an arm calibrated another way, which the connect otherwise
  refuses. Every goal that policy answers is still clipped to this arm's travel and counted, a
  goal held there for a second still ends the segment, and the step cap is the same, so the
  arm goes nowhere it could not go without the flag. What changes is that a policy whose frame
  may not be this arm's is the one choosing the goals inside it, so it may pin a joint at the
  edge of its travel. Give it only when you know the two frames match, and the run's record
  says it was given.
- `stop` holds position and never releases, and it leaves the gripper's goal alone so a failed
  verb never drops what is held. It also writes no goal for a joint that reads past its
  calibrated travel, because the servo would clamp "stay here" to its limit and drive the joint
  there at full speed, and its summary names each joint it left alone. That skip avoids starting
  a rise out of a fold and cannot halt one already under way: any goal quackd writes to a joint
  while it reads past its travel is the limit to the servo, so a joint a move had begun lifting
  keeps rising to that limit whatever the stop does. The power switch is the only stop for that
  stretch.
- **The arm falls when a session ends, unless you have recorded a rest pose.** LeRobot's own
  `disconnect()` disables torque by its default and quackd asks for that at the end of every
  clean session over an arm with no rest pose recorded, which is why the arm fell at the end of
  every run on 2026-09-15. Fold the arm by hand while nothing is
  connected, record where it sits with `quackd robot rest-pose <name>`, and quackd drives it
  back there between the `stop` and the close, on every exit path there is: success, failure,
  infeasible, a spent budget, an abort, an error and Ctrl-C. It drives the arm there at the
  start of a run too, before the pilot gets control, and a run that cannot reach it aborts
  before the first LLM call. Only the five body joints are ever driven: the gripper is recorded
  and never commanded, for the same reason `stop` leaves it alone. With no pose recorded, the
  old behaviour stands and the arm sags where it stopped, which is also what
  `quackd robot rest-pose <name> --clear` returns you to. An MCP session does the same at both
  ends and refuses to open at all if it cannot get there, because a client is about to drive a
  body nobody has established the pose of ([mcp.md](mcp.md)).
- **A rest pose past the calibrated travel is parked at the edge of it.** A servo on this arm is
  never driven outside the travel its calibration recorded: LeRobot writes that travel into it
  as two limits, and it clamps every goal to them. A folded arm can still sit past them, because
  a reading is not clamped, and on 2026-09-23 a recorded fold did, about 20 degrees past the
  floor of `shoulder_lift`'s travel. The rest move could not get there, runs aborted before the
  first model call, every run that got to its end kept torque on, and the `stop` at the end of a
  run hauled the folded shoulder up out of its fold. So the rest move now drives each joint
  clipped into its travel, a joint recorded past it is at rest at the edge or anywhere beyond it
  on the fold's side, torque is released there, and the run says once which joint is free to
  settle the rest of the way. Calibrate with every joint taken all the way into the fold, then
  record the pose, and the fold is inside the travel to begin with. A new calibration moves the
  zero of any joint whose travel it records differently, so record the pose again after one
  ([adapters/lerobot.md](adapters/lerobot.md#a-pose-past-the-travel),
  [ADR-0045](adr/0045-a-rest-pose-the-calibration-cannot-reach.md)). Whether a joint let go at
  the edge settles onto its fold gently has not been watched on an arm yet, so watch the first
  one with a hand near it.
- **An arm that did not reach that pose keeps torque instead of letting go.** quackd turns
  LeRobot's flag off for that one case, leaves the arm holding itself up, and prints one line:

  ```
  the arm is not at its rest pose (...), so torque was left on and it will not fall as it stands: hold it first, because connecting takes torque off every motor for a moment, then run quackd robot release NAME, or quackd doctor --robot NAME to park it, or cut its power
  ```

  The brackets name the joint furthest from where it was asked to be, what it reads, and why
  nothing put it there. That is a joint stopped short *inside* its travel: one parked at the
  edge of its travel, or folded past it, has reached the pose. `NAME` is the name the arm was
  registered under wherever quackd knows it, and stays `NAME` where it does not, which is a
  bare spec such as `doctor --robot lerobot:real`. Hold the arm first, then do one of the three
  things the line says. The arm is energised, the run is over, and nothing is going to put it
  down on its own, but "it will not fall" is true of the arm as it stands and of nothing that
  connects to it: `quackd robot release` lets it go into your hands, `quackd doctor --robot`
  tries the rest move again from wherever it now is and lets go at the pose if it gets there,
  and both begin with a connect that takes torque off every motor for a moment. The power
  switch works when neither of those can reach the arm. A run at a terminal offers the first of
  them itself, before the line is printed, and only over an arm that answered: an arm that went
  quiet, which is what cutting its supply looks like, gets a line that says quackd cannot tell
  whether it is holding itself up, and to hold it and cut its power.
- **A probe and a dry run now leave torque on where they used to drop it.** A dry run never
  moves the arm and `quackd robot list --probe` never moves it either, so on an arm away from
  its recorded rest pose both end with torque on: the dry run prints the line above, and the
  probe says `ok, torque left on: not at its rest pose` in its `reachable` column. Its connect
  still takes torque off every motor for a moment, with no warning printed first, so hold an
  arm that is holding itself up before you probe it.
  `quackd doctor` is the other way round: it drives a probed arm back to the pose and says which
  it got in a `rest pose` row, because a doctor probe disconnects like anything else, and that
  is one of the ways the arm fell.
- **A second Ctrl-C during the end-of-run rest move skips the close entirely.** The move is not
  shielded from a `KeyboardInterrupt`, so the process exits with quackd's close never called,
  torque never disabled, and the arm holding wherever the move had got to. LeRobot's own
  disconnect can still run as the process lets go of the arm, and it keeps torque, because
  quackd builds the arm asking it to and asks for the release itself only where it may let go.
  In 0.14 and before the arm was built asking that disconnect for the release, so it could fall
  there. A crash that skips the close ends the same way. That is the safe direction and it is
  not a clean exit: the port closes with the arm still energised, so hold the arm and run
  `quackd robot release <name>`, or cut its power.
- A good first contract is the shipped `lerobot-lookout`, which moves no joint. The order to
  bring one up in, nothing moving until step 10:
  [lerobot-hardware-checklist.md](lerobot-hardware-checklist.md).

**A wheeled base over rosbridge:**

- No deadman was verified anywhere in that stack, so if quackd dies mid-verb the base
  keeps its last Twist until its own driver times out, if it does at all. Test on blocks
  with the wheels off the ground before testing on the floor.
- quackd re-sends the Twist at 10 Hz while a verb runs and publishes a zero Twist on
  `stop`, on close, and when the heartbeat fails. That is the entire stop authority.
- The speed limits are quackd's caution (`limits.max_vx`, `max_wz` in the manifest), not
  the base's capability. Lower them before the first real drive.
- A good first contract: `allow: [observe, report_state, introspect, stop]`. Nothing there
  publishes a Twist, so you can read what the bridge says the robot is before you drive it.

**An Open Duck Mini v2:**

- **If it falls, quackd cannot pick it up, and on hardware it cannot tell that it has.**
  There is no get-up policy, so `stand_up` does not exist for it, and nothing on the bridge
  backend detects a fall: no verb refuses because the duck is down, and every observation
  says `fall-blind`. You are the fall detector: keep it on a stand until you trust the link.
- The deadman is quackd's own, and it runs on the robot. quackd's bridge daemon zeroes the
  velocity after 300 ms of silence, inside the call the control loop makes every tick, so a
  server thread that is starved, wedged or dead still stops the duck. Test it by pulling
  your laptop's Wi-Fi mid-walk before you rely on it.
- Going limp is unreachable rather than forbidden: the only channel from the network to the
  body is seven floats and a few buttons, so no message reaches a torque register.
- Head control is off unless you start the daemon with it on, and then it is clamped to 80
  percent of the runtime's own range and rate limited. Upstream warns that head control can
  break the head, and the four head values are offsets added to wherever the walk policy is
  holding the head, not absolute angles, so that clamp bounds an offset rather than a joint.
- The Feetech serial bus has exactly one owner. The bridge *is* the walk loop, so do not run
  it and upstream's script at the same time.
- The bridge binds loopback and wants a token, because a port that walks a robot on a shared
  network is a hazard. Prefer `ssh -L 9871:127.0.0.1:9871 your-pi`.
- The camera is a second process (`quackd_duck_camd.py`) serving one JPEG over HTTP with
  **no authentication at all**. It binds loopback and warns if you bind it wider, because
  it shows whatever the robot can see. Tunnel it rather than exposing it.
- The only e-stop is the power switch.
- A good first contract is the shipped `open-duck-lookout`: it looks and speaks, and moves no
  leg.
- The order to bring one up in, feet off the ground until step 10, with an abort condition
  at every step: [open-duck-hardware-checklist.md](open-duck-hardware-checklist.md).

## On other bodies

quackd drives more than the duck, and the honest answer to "what stops it when
quackd goes quiet" differs per body. Each manifest says so
(`safety_authority: {native, deadman}`), and `stop` always means stop, never collapse:

| Body | Native authority | What `stop` does | Never sent |
|---|---|---|---|
| Microduck (`microduck:*`) | `robotd_deadman`: velocity zeroes when intents stop | `robot.stop` | `robot.relax`, `robot.init` |
| LeRobot arm (`lerobot:*`) | `torque_limit`: the gripper's torque and current caps and nothing on the five body joints (`extras.torque_limit_scope` is `gripper_only`), plus a capped step per action that quackd sets; no deadman, a position-controlled arm holds its goal | re-sends the present position as the goal (hold) for each of the five body joints that reads inside its travel, writes none for one that reads past it, and leaves the gripper's goal alone. The rest move that follows a run's last `stop` is the only thing that puts the arm down | `disable_torque`, of quackd's own accord: only a person holding the arm asks for it, with `--by-hand` at the rest pose, or with `quackd robot release` or the Enter a run offers when its last rest move missed, wherever the arm stands. LeRobot's own `disconnect()` still does, by its default, at the end of a session, but only where quackd's close asks it to, once the arm has been driven back to its recorded rest pose, or to the edge of its travel where that pose lies past it: an arm that did not get there is left holding itself up, with one line saying so, and so is an arm whose exit skipped the close. With no rest pose recorded, a clean session ends the way it always did and the arm sags |
| rosbridge base (`rosbridge:*`) | `none`: neither rosbridge nor the driver has a deadman we verified | publishes a zero Twist; quackd also re-sends the Twist at 10 Hz while a verb runs | silence |
| Open Duck Mini v2 (`open_duck:*`) | `none` in the robot, but quackd's own bridge daemon runs on it and zeroes the velocity after 300 ms of silence, inside the 50 Hz loop | zero velocity, head held, torque still on | anything that reaches torque, the head-control mode button, any direct servo or IMU read |
| XLeRobot (`xlerobot:*`) | `none`: the host's own 500 ms watchdog is real but calls `stop_base()`, which zeroes the three wheels and **nothing else**, so the 14 arm and head servos keep holding under torque. `deadman_scope` says `base_only` | zeroes the three velocity keys and leaves every arm goal exactly where it was, deliberately not rebuilding a hold from an unstamped reading that may be cycles old | `disconnect()`, which is upstream's torque-off, and any `enable(on=False)` |
| AlohaMini (`alohamini:*`) | `none`: the host's 1 s watchdog calls `stop_motion()`, which is the base and the lift and never the arms. `deadman_scope` says `base_and_lift_only` | one payload carrying all three velocity zeros **and** a lift velocity zero, because omitting either leaves the robot travelling | anything that disables arm torque. As shipped the arms are already limp, which is why the arm verbs need quackd's own host wrapper and refuse without it |
| ToddlerBot (`toddlerbot:*`) | `none` in the robot, and nothing upstream has a watchdog, timeout or e-stop at all. quackd's own daemon runs on it and after 500 ms of silence slews to the safe pose at upstream's own 0.3 rad/s, waist first, and **holds** | holds the last verified-good measured pose. There is no velocity at this hardware boundary, so `stop` cannot mean zero velocity | torque off, ever. Silence on this body means hold forever and torque off means fall, so the deadman is a trajectory rather than a message |

The verbs a body lacks are not gated, they do not exist: an arm cannot `move`, a base
cannot `say`, and `validate --robot` says so before a run starts.

What each body can carry, reach and survive is its **datasheet**
([manifest-spec.md](manifest-spec.md)): every number with how sure quackd is of it and who
says so, and a figure the maker never published listed as not published rather than guessed
at. The pilot is shown it and told to judge the task against it before anything moves, which
is what `assess_task` is for, and a `.duck` file can correct it for the build in front of
you ([ADR-0032](adr/0032-datasheets-and-the-verdict.md)).

## What quackd does not protect against

A model that is *allowed* to `walk` can walk into a wall; the sim has walls, your living
room has stairs. The allowlist is your tool: a `.duck` for a new space should start small.

The feasibility verdict is the model's own guess about the world, weighed against numbers
that carry confidence labels of their own, and neither is a measurement. A wrong `feasible`
buys nothing past the gates below it: the allowlist, the budgets, the confirm gates and the
body's own safety authority all still apply, which is why the verdict is a layer above them
and not a replacement for any. A wrong `infeasible` costs one run. A robot that reports a
URDF that does not match the robot is believed, and that assumption is recorded as one.

There is one more thing to know about. A robot's memory
([memory.md](memory.md)) is text a model wrote, kept on disk, and handed to the *next*
model as part of its system prompt. The executor never reads it, so a note cannot widen an
allowlist, lift a budget or open a confirm gate: none of the guarantees above depend on it
being true. What a note can do is persuade a later run, including a later run of a different
task on the same body. A model that concludes something wrong ("the sofa is safe to walk
under") will keep telling itself so until somebody deletes the line. That is the whole point
of the feature and also its whole risk, which is why the file is plain text you can read,
`quackd memory show` prints exactly what the pilot was told, `quackd memory clear` forgets
it, and `--no-memory` runs as if it were never there.

There is a second thing that is not about the body. `--robot microduck:mujoco` downloads an
MJCF, 38 meshes and two ONNX policies from GitHub and the Hugging Face Hub the first time it
runs, into `~/.quackd/cache`, and then runs those policies in quackd's own process. What guards
that: the commit and the revision are pinned in `adapters/microduck/src/quackd_microduck/sim3d/upstream_api.py`, every file is
checked against a sha256 recorded when it was read and a mismatch is a refusal, and the tarball
is unpacked by name against a fixed list rather than by whatever it contains. What does not:
`QUACKD_MICRODUCK_ASSETS` points quackd at a checkout of your own, and there a file that differs
from the pin is a warning and the run continues, which is deliberate, because a newer export is
what somebody with a checkout usually wants. The state says which you got
(`extras.model_pinned`), so the transcript records it. Nothing in this path reaches a robot: the
physics backend has no address and drives nothing outside the process.

Report anything that lets a model bypass the executor — see [`SECURITY.md`](../SECURITY.md).
