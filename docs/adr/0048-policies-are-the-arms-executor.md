# ADR-0048: Policies are the arm's executor, and quackd owns the bus

**Status:** accepted · **Date:** 2026-09-28 · Amends [ADR-0003](0003-three-loops.md) (the arm's policy loop runs in the steering tier, in quackd's process, at the policy's own rate), [ADR-0004](0004-verb-registry.md) and [learned-verbs.md](../concepts/learned-verbs.md) (a learned policy on the arm is one of the arm's own verbs, not a learned verb), [ADR-0017](0017-robot-adapters-and-manifest.md) and [ADR-0018](0018-core-verbs-extensions-aliases.md) (a v3 task file narrows one verb's parameter schema to its own instructions, and the core knows two verb names on any body), [ADR-0036](0036-what-the-arm-does-not-say.md) (a policy's step cap is the verbs' speed at the policy's rate, and a joint past its travel is outside what it covers) and [ADR-0040](0040-a-discrete-stepper-in-front-of-the-model.md) (the stepper is shown `manipulate` and never takes it) · Implemented in `adapters/lerobot/src/quackd_lerobot/policy/`, the arm's `verbs.py` and `real.py`, `quackd/duckfile/narrow.py`, `quackd/agent/providers/vla.py` and `quackd policy` in `quackd/cli.py` ([page](../guides/policies.md), [the arm's page](../adapters/lerobot/README.md#manipulate-and-the-loop-a-policy-runs-in))

## Context

A language model on the SO-101 decides about once every six seconds. The run at the top of the
README spent 62 of its 79 seconds waiting on the model, over ten calls. That is the right rate
for choosing what to do next and the wrong one for contact, where what the gripper touches
changes in tenths of a second: Rok reports a pen grasp that a model drove turn by turn, and that
failed after twelve turns.
LeRobot's learned policies are made for exactly that part: a vision-language-action policy such
as SmolVLA or Pi0.5, or an ACT trained on a person's own demonstrations, reads the arm and its
cameras many times a second and answers with goals for every motor. What none of them has is a
judgement of the task. A policy is told one short subtask, in the words it was trained on, and
it says nothing about whether it did it.

So the arm gets two loops above its servos. A model plans, one subtask at a time, and judges
each from a fresh look at the arm. A policy executes each subtask for a short segment. Before
this decision `pick` already handed the arm to a policy, but nothing in production loaded one:
`make()` had no policy parameter, no flag named one, `load_policy()` had no caller, and it could
not have worked (`LOAD_POLICY`), since it handed LeRobot's pre-processor an observation
`build_inference_frame` would have shaped first. A successful `pick` also left its policy
driving the arm through the pilot's thinking, and nothing reset it between picks.

The plan for this was checked against the code, against LeRobot 0.6.1 as the lab ran it and
against upstream before anything was written, and these are what the checks turned up:

- A LeRobot 0.6.1 checkpoint carries no rate (`CONFIG_HAS_NO_RATE`), so the rate has to come
  from somewhere a person or the training data names.
- A checkpoint's processor files are code. Loading one imports whatever class its JSON names
  (`PROCESSOR_CLASS_IMPORT`), the action tokenizer trusts a repository's own code by default
  (`TOKENIZER_TRUSTS_REMOTE_CODE`), and SmolVLA fetches its backbone by a name with no
  revision.
- `make_robot_action` turns one row of a chunk into a goal per motor, not a chunk.
- A step cap given as a NaN switches upstream's cap off rather than refusing.
- The verbs' step cap, sent thirty times a second, is three times the speed it allows a verb
  sent ten times a second, and it is the only bound a policy's speed has: FLUX 3 Action's own
  card says nothing in the model bounds joint velocity.
- A bare `await` on a task that something else cancels ends the awaiting verb cancelled too.
- LeRobot's own policy server unpickles in both directions over an insecure port, needs torch
  in its client, skips an observation near the last one, and does not serve FLUX 3 Action.

Where the code settled differently from the plan, it is said below, and the plan is not the
record: this is.

## Decision

**A learned policy is the arm's executor, and the model is its planner.** Two of the arm's own
verbs hand it the arm for one segment. `manipulate(instruction)` takes the subtask in a few
words and runs for its seconds, 10 s unless a task file says otherwise (`MANIPULATE_S`), or
until its chunks are played, the policy says it is done, or the arm stops moving under it for
1 s (`STALL_S`). `pick(target, max_s)` runs until something is held, which the loop judges on
its own reads each tick, and stops the policy there. Both are confirm-gated and need torque on
and nothing hot. `manipulate` is ok only when its segment ran, on its seconds, its chunks or a
stall, and its summary never says the task is done. A starved policy, a guard, an error and a
stop end it failed, with the arm held, so the executor's rule for a verb that keeps failing
still ends a run that does. A pilot whose verbs include `manipulate` is told in its prompt to
hand the policy one short subtask per call and to judge each from a fresh look, and never from
the verb's ok. `manipulate` is not one of the verbs that run before a verdict
(`verdict.BEFORE_VERDICT`), so the feasibility verdict comes first, and it is classified with
the verbs that move the body (`verdict.MOVES_THE_BODY`), which the gate never reads and a test
holds every shipped verb to.

**The loop is quackd's, in the arm's process, at the policy's rate.** A segment is the backend's
policy task, and its body is `PolicyLoop` (`policy/loop.py`). Every stop path cancels that task
first, and the verb waits on it with `asyncio.wait`, so a stop from the heartbeat, from MCP or
from a verb refused meanwhile ends the verb as `stopped:` and says which. The rate is the
policy's own, from a source it names, and one that is not a finite number from 1 to 60 Hz
refuses the segment. Tick `k` is due at the start plus `k` periods, on the arm's clock, and a
tick that overruns skips to the next whole period and never sends twice in one. A chunk's goals
for ticks already played are dropped as it arrives and the rest replaces what was queued. A
tick with nothing queued sends nothing, and 1 s of that ends the segment, 5 s for the first
chunk. The policy's calls run on a worker of their own, never on the bus's threads. On the
simulator the policy's answer costs no sim time and is let go its declared latency later, where
it would have landed on the arm, so a rehearsal plays the arm's trajectory and proves no rate.
This is the steering tier of [ADR-0003](0003-three-loops.md), run at the policy's rate rather
than the composites' 10 Hz, and it calls no model. The reflexes are still each servo's own
position controller.

**Every tick is judged before it sends.** A hot joint, torque off, a camera that gave no frame,
an action that is not a finite number or names no motor of this arm, a goal held past the travel
for 1 s, three failed sends in a row and a read the arm did not answer each end the segment with
the arm held. A joint reading outside its travel is left out of every action, as a stop leaves
it out, and a segment refuses to start with one more than 2 degrees outside. A policy's goal
past the travel is clipped and counted, as [ADR-0036](0036-what-the-arm-does-not-say.md)
decided, where a verb's is refused.

**A policy moves the arm no faster than a verb may.** The cap is the verbs' own speed,
`max_step_deg / TICK_S` degrees a second, 50 at the default, so `QUACKD_LEROBOT_MAX_STEP_DEG`
governs both. Per send it is that speed over the policy's rate, and never more than one verb
step, so a policy at 30 Hz is capped at about 1.7 degrees a send where a per-tick cap would
have let it move three times as fast. It is written on the follower's config for the length of
the segment, put back to `max_step_deg` in the segment's `finally` however it ends, and written
again before every hold, rest move and verb's send, so no path round that `finally` leaves a
policy's cap under a verb. **What the cap does not cover is a joint past its travel.** LeRobot
caps each send to within a step of the reading, and a step from a reading past the travel is
still past it, so the servo clamps it to the limit and drives there at its own speed. That is
why such a joint is left out of every action: a policy never starts that rise. It cannot halt
one a move had already started, and the power switch is still the only stop for that stretch.

**No quackd command loads a checkpoint in the process that owns the serial bus.** A checkpoint
is code, so the policy runs in a server of its own, `quackd policy serve`, which the user
starts in a terminal of its own, on the laptop beside the arm or on a rented GPU reached through
`ssh -L`, the way the Jetson's daemon is started
([ADR-0046](0046-the-jetson-is-reached-not-run-on.md)). The arm's process reaches it with
`RemoteRunner`, a client made of `http.client` and PIL that needs no torch and no LeRobot, so
`lerobot:mujoco` on Python 3.11 reaches a server as `lerobot:real` does. The server reads
before it builds: `config.json` and both processor JSONs first, at the revision named, refusing
a policy type other than ACT, SmolVLA and pi05, a step named by a `class` key and any registry
name outside the ones those three processors use, before any weights are fetched. It tells a
step that could trust a repository's code not to. A model the checkpoint names inside itself is
refused unless `--pin REPO@REVISION` fixes it at a whole commit or a tag, and is fetched with no
`.py` and no pickle, and its cache directory is refused if it holds any other kind of file or a
config that maps a class to code. Every weight is loaded strictly, since LeRobot's loader only
logs a missing one and pi05's returns a random network when its weights do not load. The rate
is `--fps`, or the fps of the dataset the checkpoint's `train_config.json` names, taken only at
a whole commit or a tag the Hub says is one, or the server refuses to start. The one path in
quackd past all of this is `load_policy()`, still in `real.py`, which no command reaches, and
Consequences says what that gap is.

**The server is quackd's, not a daemon under `bridge/`.** It shares the pipeline and the
protocol with `quackd policy check`, which serves a policy for the length of a check and
streams synthetic observations through the real client, and a daemon under `bridge/` imports
nothing of quackd's. It is in the arm's package, behind its own extra, `quackd[lerobot-vla]`,
which is LeRobot with torch and transformers on Python 3.12 and no `[feetech]`, since the
server never opens a serial port.

**The protocol is four JSON calls over HTTP, with every number checked.** `GET /v1/policy` says
what is served: the policy and every repository loaded at its revision, the features, the rate
and its source, the chunking, whether it is asked every tick, whether there is a GPU, the
quantiles it learned from, its declared latency, its threads and how it wants its frames.
`POST /v1/reset` starts a session with the instruction, the bus's motors in the bus's order
and each camera's name and size. `POST /v1/step` carries the session, a sequence number, the
tick, the state, a frame per camera and the command the arm last sent, and answers a chunk or
nothing, with the session and sequence echoed. `POST /v1/end` ends a session. The server listens
on 9875, the port after the Jetson daemon's. A token is always required, read from one header
and compared in constant time, and the server writes one to `~/.quackd/policy.token` when it is
given none. It binds `127.0.0.1` or `::1` unless `--behind-tls` says a proxy stands in front,
and the client sends plain HTTP to those two addresses and nowhere else, refuses `localhost`,
follows no proxy and no redirect, and keeps the token out of every error. JSON's `NaN` and
`Infinity` are refused on both sides and every body, reply and chunk has a bound. A reset from
another client is refused while a session is in use, so `check --bench` against a server an arm
is driving through never ends the arm's segment, and a step sent again on a new socket is
answered from its first answer, never inferred twice.

**The arm checks the policy before any torque, and again at every segment.** Once its cameras
are open and before a motor is energised, the connect refuses a policy whose state or action is
not as long as the bus has motors, whose action names are not the bus's motors in its order,
that looks at an image no camera gives (SmolVLA and pi05 run with it padded, and the record says
so), that learned at another frame size, or whose learned state's 1st and 99th percentiles lie
outside this arm's calibrated travel. A server started again with another policy since the
connect starts no segment. The last of those refusals is the one a person can override, with
`--accept-other-frame` beside `--policy-url`, for a policy they know learned in a frame that
matches this arm's. The override lets the policy connect and nothing more: every goal it answers
is clipped to this arm's calibrated travel as any policy's is, so it changes what drives the arm
and never where the arm may go, and the run's record says it was given.

**Only the flag names a server.** `--policy-url` and `--policy-token` are on `run`, `preflight`
and `serve-mcp`. The token has a variable, `QUACKD_POLICY_TOKEN`, and the address has none and
no registry field, because one line in a `.env` would otherwise put a policy in charge of every
run. `make_adapter` and `describe` pass `policy=` only when one is given, and refuse, naming
the arm, an adapter whose `make()` or `describe()` has no such parameter, which is every body
but the arm. Of the arm's backends `lerobot:real` and `lerobot:mujoco` take one, the mock,
which scripts its own, refuses it, and a flock refuses one, since one server drives one arm.
`describe(policy=...)` puts `pick` and `manipulate` in the static manifest, so a task that
allows them is judged before anything connects. Over MCP both need `serve-mcp --yes`.

**A goal run given a policy allows `manipulate`, behind a confirm.** A goal's contract allows
the safe verbs and nothing else (`duck_from_goal`), and `manipulate` is not safe, so a goal
could never have used a policy. With `--policy-url` the goal duck allows it and lists it under
`confirm`, so a person at a terminal is asked before each segment unless `--yes`, or a pipe or
file on stdin, answers for them. Without a policy server a goal is what it was.

**`--yes`, or a pipe on stdin, takes the person out of a policy run.** `quackd run --yes` builds
the executor with `allow_all` as its confirm, as it always has. Without `--yes` the confirm is a
y/N question read from stdin, so `yes | quackd run` and `quackd run < answers.txt` answer it
too, as they always have ([safety.md](../concepts/safety.md#who-the-record-says-was-asked)). Either way
nobody is asked before a `pick` or a `manipulate` the pilot calls, a goal run's included, and
the pilot's word and a yes nobody said are all that start a learned policy driving the arm. The
record says so: the gate's reason is `the confirm gate was allowed`, and no `prompt` event names
a person. Over MCP both verbs need `serve-mcp --yes`, so there quackd asks nobody before any
segment, and `quackd preflight` clears every segment the same way, on the simulator alone.
`--controller vla` refuses `--yes`, and a run with no terminal to ask on, since its verdict is a
person's. What such a run keeps is every rule that asks nobody: the allowlist, the narrowed
instruction, the segment gate, `policy.total_s`, the step cap, the guards each tick and every
stop.

**A task file holds `manipulate` to its own words and seconds.** A `duck: 3` file's `policy`
section lists the instructions the policy may be told, at most 12 lines of at most 200
characters, how long each segment runs, at most 60 s, and how long they run in all, at most an
hour. One helper, `quackd.duckfile.narrow`, rebuilds `manipulate` from the verb the body
registered every time the verbs a run offers become final: its `instruction` becomes an inline
enum of the list, its timeout the segment plus 10 s, and on the simulator the wall time the
clock stands still while the policy thinks, and the segment reaches the backend through
`set_segment_s`. The executor charges every segment of `pick` and `manipulate` against the
section's `total_s` (`Budget.policy_s`), one segment at a time (the `segment` gate), and a run
with no section gets 10 s segments and 120 s of them. A list refuses `pick` beside it, since
`pick` tells the policy a target of the pilot's own. This amends
[ADR-0017](0017-robot-adapters-and-manifest.md) and [ADR-0018](0018-core-verbs-extensions-aliases.md)
twice. A verb's parameter schema no longer comes from its adapter alone: a task file, which is
untrusted input, narrows one verb's schema, never widens it and never adds a verb. And the core
treats two verb names as its own. The executor charges and gates `pick` and `manipulate`
(`POLICY_VERBS`) by name, the narrowing helper, the stepper and the prompt's executor section
find `manipulate` by name, and a list refuses `pick` by name, on any body that registers a verb
by either name. Before this the core named a body's verbs only to say which of them move it
(`verdict.py`), and ran them all alike.

**The stepper is shown `manipulate` and never takes it.** Narrowed to a list, it is a closed set,
and which subtask comes next is exactly a between-segment choice. But it is confirm-gated, and
under `--yes` nobody is asked at that gate, so a stepper that cleared the confirm floor would
start a learned policy driving the arm with no person and no model involved. It is offered, an
answer that clears every other gate ends on `gate: shadow_only` (`SHADOW_ONLY`), the model takes
the turn, and in `--decision-mode on` as in shadow a `decision_shadow` record sets the two
answers side by side on every turn that offered one. That is a curated exception to
[ADR-0040](0040-a-discrete-stepper-in-front-of-the-model.md)'s computed classification, and
promoting it needs a measured agreement rate and a decision of its own.

**Two controllers.** `--controller llm`, the default, is the model `--llm` names deciding each
subtask, with `manipulate` in its vocabulary when `--policy-url` gives the arm a policy.
`--controller vla` is a scripted pilot (`providers/vla.py`) with no model at all. It answers
`assess_task` with `uncertain`, so a person decides whether the arm tries, hands the policy
each of the task file's instructions in order, or the `--goal` as the only one, with one
`manipulate` apiece through the same narrowed verb, confirm gate and budget as any pilot's, and
then the loop asks `Did the arm do it?` (`RunConfig.judge`). It is a `JudgedPilot`: only a yes
from a person really asked is a success, and the loop holds any such pilot to that whatever it
declares. Its calls count as the loop's calls, as the scripted pilot's do, so a task listing N
instructions takes N + 2 of `max_llm_calls`: the verdict, the segments and the declare. The
seconds a person takes to answer come off `max_minutes`. The question, like the confirm gate's
and the verdict's, is asked on the event loop's own thread, so the heartbeat does not beat
while the person answers, and the arm holds where its last segment left it, as a servo holds
the last goal it was sent. It is refused before anything connects wherever nobody could answer,
with `--yes`, with no terminal, over MCP, and with `--dry-run`, and beside a decision LLM or a
flag for a model or a picture it would ignore. It reads the verb's result and the person's
answer, never the simulator's truth.

**FLUX 3 Action is not claimed.** It is on LeRobot's main branch only, after 0.6.1, and needs
Linux, an NVIDIA GPU, a NATTEN built for the machine's torch and CUDA, and about 32 GB of GPU
memory in BF16 by its makers' own report. Its official SO-101 checkpoint predicts actions as
deltas, integrated against the last command every tick, so it is a policy asked every tick with
the command sent fed back. quackd's server serves ACT, SmolVLA and pi05 and refuses any other
policy type, FLUX's included. What would change that is a spike on a rented Linux GPU, which
has not run, that shows three things: that `quackd policy check --bench` holds the checkpoint's
rate through the tunnel, that its delta integration stays anchored when quackd clips a goal,
and that the official SO-101 checkpoint at a pinned revision drives the simulator end to end.
Its refs would get a table of their own, pinned at the LeRobot commit that added it.

**Where the code settled differently from the plan.**

- The plan had the server lower its own priority. It does not: torch gets one thread fewer than
  it would take unless `--threads` says, and `/v1/policy` reports the count, which the run's
  record does not keep.
- The plan had the heartbeat accept a read the policy loop made within one period. It probes on
  its own every beat instead, because a read that went out before the beat asked could pass an
  arm that died as it came back. A beat during a segment costs the loop one read.
- The plan had a documented override for a frame of another size and for a policy learned in
  another frame of reference. The second is `--accept-other-frame` on `run`, `preflight` and
  `serve-mcp`, refused without `--policy-url`. It began as a keyword of `RemoteRunner` alone,
  which a refusal named and nobody on the command line could reach, and on the lab arm's
  calibration 55 of the 68 servable SO-100 and SO-101 ACT checkpoints on the Hub were refused on
  `shoulder_lift`, whose recorded travel there does not reach the arm's fold
  ([ADR-0045](0045-a-rest-pose-the-calibration-cannot-reach.md)). The first stays a keyword,
  `accept_frame_size`, and its refusal says to give the camera the checkpoint's size with
  `--camera-url`'s `width=` and `height=`, which every camera quackd opens takes. The record
  says when either was taken.
- The plan had frames go as JPEG on every remote link. The server says how it wants them: raw,
  or JPEG at `--jpeg-quality`, and 90 when it is behind TLS and told nothing. A tunnel looks
  like loopback to both ends, so a server reached through `ssh -L` gets raw frames unless it
  is given a `--jpeg-quality`.
- The plan had three calls. The code has a fourth, `POST /v1/end`, so a client that is done
  frees the server at once, and one session at a time: a reset from a second client is refused
  while the live session is in use, so a check against a server an arm is driving through never
  ends the arm's segment.
- The plan did not say what a pi05 that learned relative actions needs. It is served a whole
  chunk at a time, made absolute against the state it was predicted from, as its training made
  it relative, where LeRobot's own loop makes each action absolute against the state of the
  tick it is played at (`VLA_PIPELINE`).
- The plan had `policy/flux_upstream_api.py` made in the spike. The spike has not run, so the
  module does not exist.
- The plan had a policy asked every tick go through a delay line `k` ticks deep on the
  simulator, as a chunk is held back `k` ticks. The code's line is a tick deep at most. Such a
  policy is asked again only once it has answered, so one that declares more than a tick to
  answer could never keep up on the arm, and the segment refuses it on either clock rather than
  rehearse it on the simulator's (`PolicyLoop.start`).

## Why not

**LeRobot in the arm's process.** A checkpoint would import the code its processors name in the
process that holds the serial bus, the heartbeat and the stop, and a hang in inference would
share that process's threads with every read and hold. The arm's process would need
transformers besides LeRobot, and the simulator, which installs on 3.11 with MuJoCo alone, would
need both. `load_policy()` was that path, and nothing ever called it.

**LeRobot's own gRPC policy server.** It unpickles every observation and every chunk over
`add_insecure_port`, so anything that reaches the port runs code on the other side
(`ASYNC_PICKLE`). Its client imports torch (`ASYNC_CLIENT_NEEDS_TORCH`), which is the process
quackd keeps free of it. It skips an observation whose joint state is near the last one it ran
(`ASYNC_SKIPS_SIMILAR`), which is how a policy stops seeing an arm that is barely moving. And
its list of policies has no FLUX 3 Action (`ASYNC_SUPPORTED_POLICIES`).

**A child process spawned by `quackd run`.** One command instead of two terminals is the
better experience, and on Windows it is five problems v1 does not need: delivering Ctrl-C to
the child, an orphan left serving when the parent dies, a port already taken by the last one,
the child's output interleaved with the run's, and which environment the child runs in, since
the server wants Python 3.12 and torch and the arm's process may be neither. The server the user
starts is the Jetson daemon's arrangement, which already works. A worker spawned over a pipe can
follow when somebody needs it.

**`register_learned_verb`.** It registers a verb with no parameters, so a policy could never be
told a subtask. It is shaped for an ONNX policy the Microduck runs, and it registers into a
registry that the connect rebuilds from the manifest, so the verb is gone by the time a run
starts. It stays the reserved extension point [learned-verbs.md](../concepts/learned-verbs.md) describes,
for skills trained from rewards. A policy the arm already runs is an arm's verb, as `pick`
always was.

## Consequences

- **Nothing here has driven the arm.** Every segment has run against the test suite's fake arm
  and on the simulator. CI loads a tiny random ACT, and on 2026-09-28 a trained ACT from the Hub,
  `natsuki0000/act-so101-bluecap` at commit `82f75fe40a311026b4f7cacdea7bf14cadc44ccd`, was
  served on a laptop's CPU and drove a twin of the lab's arm on the simulator over
  `serve-mcp`. On 2026-09-29 OpenAI's `gpt-6-sol` flew that twin with `quackd run --goal` and
  `--policy-url`, handed the same ACT two `manipulate` segments and declared failure on seeing
  no blue cap, since the simulator's table holds a red cube and a pen. That run proves the
  plumbing and nothing about the task. SmolVLA loaded on that laptop and took minutes a chunk on
  its CPU, so it never answered a step through the client, pi05 has not run, and an ACT asked
  every tick needs a GPU the CPU job lacks (`VLA_PIPELINE`, `TICK_MODE`). `--controller vla`
  and its judge prompt have run in the test suite and never with a trained checkpoint.
  `--decision-mode shadow` has run beside one, by hand, on 2026-09-28 and again on the released
  0.16.0 on 2026-09-29: `gpt-6-sol` flew the twin with that ACT served and a stub decision LLM
  loaded as a plugin, which was asked on every turn, and a `decision_shadow` record set its
  answer beside the model's on each. A second stub, one that always picks `manipulate`, chose
  it on every turn that offered it, in shadow and in `on`, and each ended on
  `gate: shadow_only` with the model taking the turn. No real decision LLM has run beside a
  policy.
- **Only the bench can say how fast the loop runs on the real bus with a server inferring on the
  same laptop.** Torch's threads and the bus's worker share one CPU there, and nothing timed on
  the simulator's lockstep clock is a rate. `quackd policy check --bench` measures the server
  and the wire without an arm, and the policy block of a run's `summary.json` says what the
  loop achieved. PLAN.md carries it as an open item.
- **What a policy does on the simulator is not evidence about the arm.** The simulator's frames
  are renders of quackd's own scene from default mounts, and a policy trained on a real
  camera's frames sees a picture it never saw, so it can fail there and work on the arm, or the
  other way round. The simulator proves the plumbing, the clips, the stops, the guards and the
  budgets, and never a rate or a grasp.
- **The stepper only shadows `manipulate`**, in both modes, until a measured agreement rate and
  an ADR of its own promote it.
- **A policy's weights are the user's download, under their own licence,** and quackd ships
  none. FLUX 3 Action's is the FLUX Kommunity License, which grants robot control, a Robotics
  Use in its words, for non-commercial purposes, and whose commercial carve-out for Qualifying
  Users covers the model's Outputs on conditions and does not say it reaches a Robotics Use.
  pi05's base card says Gemma where LeRobot's page says Apache 2.0 ([policies.md](../guides/policies.md)).
- **A second terminal is part of running a policy.** Starting the server is the user's step,
  and a run without one is refused before anything connects, with a sentence saying so.
- **`load_policy()` is a known gap.** It is a Python helper in `real.py` from before this
  decision, and it still builds a LeRobot policy in the arm's own process, beside the serial
  bus, with none of the server's reading before it builds and no check at connect that the
  policy fits the arm. Nothing in quackd calls it and no command reaches it, so only somebody's
  own Python can, and the `LOAD_POLICY` row on
  [the arm's page](../adapters/lerobot/README.md#the-policies-upstream-lerobot-061) says so. Whether it
  goes, with its test and its row, is not decided, and PLAN.md carries it as an open item.
- [ADR-0003](0003-three-loops.md), [ADR-0004](0004-verb-registry.md),
  [ADR-0017](0017-robot-adapters-and-manifest.md), [ADR-0018](0018-core-verbs-extensions-aliases.md),
  [ADR-0036](0036-what-the-arm-does-not-say.md) and
  [ADR-0040](0040-a-discrete-stepper-in-front-of-the-model.md) each carry a note pointing here,
  and none of their decisions is reversed: each is extended or narrowed as its note says.
