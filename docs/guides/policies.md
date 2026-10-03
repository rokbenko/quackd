# Policies: a learned policy as the arm's executor

On the SO-101 arm, `manipulate` and `pick` hand the arm to a learned policy for one short
segment. The pilot plans, one subtask at a time, and the policy executes: it reads the arm and
its cameras many times a second and answers with a goal for every motor, which quackd paces on
the arm's clock and holds to the arm's rules before each one is sent. The policy runs in a
server of its own, `quackd policy serve`, which you start, and a run reaches it with
`--policy-url`. This page is how to set that up, on the laptop beside the arm or on a rented
GPU, which policies it serves and under what licences, and where the data a policy learns from
comes from. What the verbs do and what refuses them is on
[the arm's page](adapters/lerobot.md#manipulate-and-the-loop-a-policy-runs-in), and why it is
shaped this way is [ADR-0048](adr/0048-policies-are-the-arms-executor.md).

> [!WARNING]
> **Nothing on this page has driven the arm.** Every segment so far has run against the test
> suite's fake arm and on the arm's simulator. On 2026-09-28 a trained ACT from the Hub,
> `natsuki0000/act-so101-bluecap` at commit `82f75fe40a311026b4f7cacdea7bf14cadc44ccd`, drove a
> twin of the lab's arm on the simulator through `quackd policy serve` and `quackd serve-mcp`,
> and on 2026-09-29 it drove the twin again under OpenAI's `gpt-6-sol` in a `quackd run --goal`.
> SmolVLA loaded on the same laptop and never answered a step in time on its CPU
> ([below](#smolvla-and-act)), pi05 has not run, and FLUX 3 Action is not served at all.
> Rehearse on the simulator first, keep a hand on the arm's power switch the first time a policy
> drives it, and send back what happened
> ([lerobot-first-run.md](lerobot-first-run.md#14-what-to-report)).

## Two processes, two terminals

No quackd command loads a checkpoint in the process that owns the arm's serial bus, because a
checkpoint is code ([below](#a-checkpoint-is-code)). So there are two:

| | The arm's process | The policy server |
|---|---|---|
| Command | `quackd run`, `quackd preflight` or `quackd serve-mcp`, with `--policy-url` | `quackd policy serve --policy OWNER/NAME@REVISION` |
| Install | `quackd[lerobot]` for the arm, `quackd[lerobot-sim]` for its simulator | `quackd[lerobot-vla]`: LeRobot, torch and transformers |
| Python | 3.12 for the arm, 3.11 or newer for the simulator | 3.12 |
| Owns | the serial bus, the pacing, the step cap, every guard and every stop | the checkpoint, its processors and the inference |
| Reaches the other | an HTTP client with no torch in it | nothing: it answers on port 9875 |

The arm's process asks the server what it serves before anything connects, checks that the
policy fits this arm before any motor is energised, and asks for a chunk of goals as each
segment runs. It never sends a goal it has not checked. On the arm a server that stops
answering starves the segment, which ends with the arm held. On the simulator, whose clock
stands still while the loop waits for a chunk, the client's own deadline ends it instead, and
the segment says the policy raised `PolicyServerError`.

## On the laptop alone

Everything on one machine, the laptop the arm is plugged into, with the policy on its CPU.
Start on [the arm's simulator](adapters/lerobot.md#the-simulator-lerobotmujoco) rather than the
arm, since a twin of your arm reaches a server exactly as the arm does.

**1. A Python 3.12 environment with the server's extra.** The arm's own 3.12 environment can
hold it too:

```bash
uv pip install "quackd[lerobot-vla]"
```

**Where the download goes.** A checkpoint, and any model it names inside itself, is fetched into
the Hugging Face Hub's cache, `~/.cache/huggingface/hub` unless you say otherwise. To keep it on
another disk, set `HF_HUB_CACHE`, which moves that cache and nothing else. Not `HF_HOME`: LeRobot
looks for an arm's calibration under `$HF_HOME/lerobot/calibration` unless
`HF_LEROBOT_CALIBRATION` or `HF_LEROBOT_HOME` says otherwise, so an `HF_HOME` set where the arm's
process runs moves where its calibration is looked for as well, and `quackd robot twin` could not
find the lab arm's file under one
([the calibration id](adapters/lerobot.md#the-name-you-give-the-arm-is-its-calibration-id)).

**2. Check the checkpoint before you serve it, and bench it twice.** `quackd policy check
--policy` serves it for the length of the check, and `--bench` times one warm step, then streams
synthetic observations through the real client at the policy's rate, paced and queued as a
segment is:

```bash
quackd policy check --policy OWNER/NAME@REVISION --bench
```

It prints the features the policy wants, its rate and where that came from, its chunks, the
percentiles of the state it learned from, every repository it loaded at the revision it loaded
it at, the rate it achieved, the ticks it had nothing to send and the ones it skipped, the round
trip, and the `--latency-s` to serve it with, read at the 95th percentile of every step it timed
from the request going out to its chunk back. Here is the first bench of an ACT trained on an
SO-101 and published on the Hub, on this project's laptop, an Intel Core i5-10210U with no GPU,
on 2026-09-28. Its training config names its dataset at no revision, so it takes `--fps`, and the
rows about the policy itself are cut:

```
$ quackd policy check --policy natsuki0000/act-so101-bluecap@82f75fe40a311026b4f7cacdea7bf14cadc44ccd --fps 30 --bench
achieved    18.7 Hz of 30: 187 of the 300 ticks in 10.0 s sent an action
starved     0 ticks with nothing to send
skipped     113 ticks: the policy declares no --latency-s, so each chunk was waited for in the tick
            that asked for it, as a segment waits for one, and the ticks that passed meanwhile sent
            nothing: the arm would have held still through them
round trip  median 620.0 ms, p99 653.3 ms, max 653.3 ms over 6 requests
inference   median 599.4 ms on the server
latency     653.3 ms or less for 95% of the 7 steps timed, from the request to its chunk back:
            serve with --latency-s 0.66, so the simulator holds each chunk back as long, and bench
            again with it
```

A first bench is served with no `--latency-s`, and a segment waits for each chunk of such a
policy in the tick that asked for it. Every tick that passes while it waits is skipped, which on
the arm is a tick that sends nothing while the arm holds still. Skipped ticks are not counted as
starved: a starved tick is one with a chunk on its way and nothing left to play. **Bench it
again**, served with the latency it suggests and with the `--fps` the first bench needed:

```bash
quackd policy check --policy natsuki0000/act-so101-bluecap@82f75fe40a311026b4f7cacdea7bf14cadc44ccd --fps 30 --bench --latency-s 0.66
```

A good second bench skips no tick, starves only while its first chunk is on its way, which a
segment gives five seconds for, achieves close to the policy's rate, and says the `--latency-s`
it was served with covers what it timed. A step's time moves from bench to bench on a laptop's
CPU, and a bench over a few seconds times only a few steps, so the one here suggested more: its
second bench timed a step at 886.5 ms and said to serve with 0.89. Served with that, and given
`--seconds 30` for more steps to read the percentile from, it looked like this:

```
$ quackd policy check --policy natsuki0000/act-so101-bluecap@82f75fe40a311026b4f7cacdea7bf14cadc44ccd --fps 30 --latency-s 0.89 --bench --seconds 30
achieved    29.1 Hz of 30: 873 of the 900 ticks in 30.0 s sent an action
starved     27 ticks with nothing to send, waiting for a chunk to come back, every one before the
            first came back, which a segment gives time for
round trip  median 634.2 ms, p99 879.8 ms, max 879.8 ms over 18 requests
inference   median 616.8 ms on the server
latency     879.8 ms or less for 95% of the 19 steps timed, from the request to its chunk back: the
            --latency-s 0.89 it is served with covers that
```

A CPU that cannot keep up shows here, before an arm is involved: skipped ticks on the first
bench, and starved ticks after the first chunk on the second. Torch gets one thread fewer than
it would take, which leaves a core for the arm's process, and `--threads` says otherwise. A
policy whose steps take longer than a segment waits for its first chunk, or longer than half
its chunk's actions, rounded down, take to play, is given no `--latency-s` at all, since `serve`
would refuse any that covered them: the bench says it answers too slowly to drive an arm from
that machine, and to serve it on a GPU.

Before it loads anything, the server reads the checkpoint's own files and refuses what it has
not been told. Here is `lerobot/smolvla_base` at a commit, checked in LeRobot 0.6.1's
environment. It names a backbone inside itself, which would load at whatever revision the Hub
has that day:

```
✗ error: lerobot/smolvla_base@d9f33c94a60fb382c90dea2164c96845bd955e28 names
HuggingFaceTB/SmolVLM2-500M-Video-Instruct as its vlm_model_name, which would load at whatever
revision the Hub has that day: pass --pin HuggingFaceTB/SmolVLM2-500M-Video-Instruct@REVISION, a
commit or a tag you have read, and the server fetches it at that revision and at no other
```

Given `--pin HuggingFaceTB/SmolVLM2-500M-Video-Instruct@7b375e1b73b11138ff12fe22c8f2822d8fe03467`,
it asks for the rate, which no LeRobot checkpoint carries:

```
✗ error: lerobot/smolvla_base@d9f33c94a60fb382c90dea2164c96845bd955e28 has no train_config.json to
say what data it learned from, and no checkpoint carries a rate: give --fps, the fps of the dataset
it learned from
```

A checkpoint you fine-tuned with LeRobot's own training has a `train_config.json` naming the
dataset it learned from, and the server reads that dataset's fps itself when the revision it
names is a whole commit or a tag. Anything else, a branch or no revision at all, gets the same
refusal, and `--fps` is the rate you recorded the dataset at. `smolvla_base` is a base to
fine-tune from, not a policy for your task: [SmolVLA and ACT](#smolvla-and-act) below.

**3. Serve it in a second terminal**, with the `--latency-s` the last bench said covers it, and
the `--fps` its checks were given:

```bash
quackd policy serve --policy natsuki0000/act-so101-bluecap@82f75fe40a311026b4f7cacdea7bf14cadc44ccd --fps 30 --latency-s 0.89
```

A checkpoint that took `--fps` to be checked takes it to be served, and without it `serve`
refuses as the check did. It prints where it serves, `http://127.0.0.1:9875`, the token file it
wrote or read, and the rate and where that came from, and serves until Ctrl+C. The first time,
it writes a token to `~/.quackd/policy.token`, readable by you alone where the OS allows, and
every client on the same machine reads it from there. `--latency-s` matters on the simulator,
whose clock stands still while the policy thinks, and which holds each chunk back that long so
it lands where it would have on the arm. The time `manipulate` is given there is sized from that
latency, so a policy that answers more slowly than it declares can run the verb out of time,
and the timeout then says so, with how long a request took and the latency declared.

**4. Point a run at it**, in the first terminal:

```bash
quackd run task.duck --robot arm-01-sim --policy-url http://127.0.0.1:9875 --llm openai
```

`127.0.0.1` and not `localhost`: the client refuses `localhost`, whose lookup costs about two
seconds a call on some machines. A server that is not there is one sentence, before anything
connects. On Linux and macOS a closed port refuses at once:

```
✗ error: nothing is listening at http://127.0.0.1:9875: start quackd policy serve, or point
--policy-url at the port it printed
  quackd policy check --policy-url http://127.0.0.1:9875 asks it what it serves
```

Windows retries a refused connection for about two seconds, a little longer than the client
waits for one, so there the same missing server reads as one that did not answer:

```
✗ error: http://127.0.0.1:9875 did not take a connection within 2 s: is quackd policy serve running
there, and the tunnel up?
  quackd policy check --policy-url http://127.0.0.1:9875 asks it what it serves
```

**A segment starts only with every joint inside its travel.** A joint that reads further outside
its calibrated travel than the slack a reading is forgiven refuses the segment before the policy
is asked for anything, and the refusal ends `Move shoulder_lift inside its travel first`, naming
the joint: a joint out there is left out of every goal a policy sends, so the policy could never
move it. A twin of the lab's arm starts that way. Its rest pose was recorded folded, past what
its calibration lets `shoulder_lift` be driven to
([ADR-0045](adr/0045-a-rest-pose-the-calibration-cannot-reach.md)) and past the model's stop as
well, so the twin starts with that joint settled just off the stop, clear of its table
([the arm's page](adapters/lerobot.md#running-it)), and its first `manipulate` is refused. Over
MCP on 2026-09-28, with `scripted:sweep` served:

```
do refused: shoulder_lift reads -99.4, outside its calibrated travel of -84.2..84.2, so the policy was not started. A joint outside its travel is left out of every goal a policy sends, because the one goal its servo takes there is the end of its travel, and the policy could never move it. Move shoulder_lift inside its travel first
```

A pilot that can call `move_joints`, a model under `--controller llm` or an MCP client, moves the
joint inside first, as that run did, to `shoulder_lift` -40, `elbow_flex` 40 and `wrist_flex` 0,
and its two segments then ran. The same run made in-process ended with the rest move parking the
joint at the edge of its travel, in the pose the twin settled to there, and the close letting it
go. A move that takes the arm further from its fold can still end with the rest move setting the
gripper down on the table short of that pose, and the close then keeps torque on
([the arm's page](adapters/lerobot.md#running-it)). `--controller vla` only ever calls
`manipulate`, so it cannot move the joint in, and its run ends in failure, in the verb's own
words. The lasting fix is to calibrate the arm again folded, so the fold lies inside
its travel, which is the bench item in [PLAN.md](../PLAN.md) that reads the calibrated value at
each of the arm's stops.

A task file that means to use the policy allows `manipulate`, and a `duck: 3` file can list the
subtasks it may be told, how long each segment runs and how long they run in all
([duck-spec.md](duck-spec.md#policy-v3)). A `--goal` run given `--policy-url` allows
`manipulate` too, and asks you before each segment when it runs at a terminal. `--yes`, or a
pipe or file on stdin, answers in your place, and nobody is asked. With `--controller vla` no
model is involved at all: a scripted pilot hands the policy each listed instruction in turn
and then asks you whether the arm did it ([the arm's page](adapters/lerobot.md#a-scripted-pilot-that-a-person-judges---controller-vla)).

**What only the arm can tell you** is how fast the policy loop runs on the real bus while the
same laptop's CPU is inferring. Nothing timed on the simulator is a rate, because its clock is
lockstep. `check --bench` measures the server and the wire, and after a run on the arm the
`policy` block of its `summary.json` says what the loop achieved: its ticks, the late ones, the
ones with nothing to send, the ticks a second and the round trip.

## On a rented GPU

Pi0.5 and SmolVLA want a GPU. The server runs there, bound to that
machine's own loopback, and the laptop reaches it through an ssh tunnel. The arm's process stays
on the laptop, with the arm.

**On the GPU machine**, a Linux box with an NVIDIA GPU and Python 3.12:

```bash
uv pip install "quackd[lerobot-vla]"
quackd policy serve --policy OWNER/NAME@REVISION --jpeg-quality 90
```

A checkpoint that took `--fps` on the laptop takes it here too. A tunnel looks like loopback to
both ends, so without `--jpeg-quality` the server takes raw frames, which cost far more bytes
over a network than JPEG does. The server refuses to bind any address but `127.0.0.1` or `::1`
unless `--behind-tls` says a TLS proxy stands in front of it, and then the laptop gives the
proxy's `https://` address and the client checks its certificate.

**On the laptop**, the tunnel, in a terminal of its own:

```bash
ssh -L 9875:127.0.0.1:9875 you@gpu-host
```

**The token.** The server wrote its token on the GPU machine, and the laptop's client looks in
its own `~/.quackd/policy.token`, which is not that one. Hand it the server's through the one
variable there is for it:

```bash
export QUACKD_POLICY_TOKEN="$(ssh you@gpu-host cat .quackd/policy.token)"
```

`--policy-token` does the same on one command. Either way the token goes in a header and never
in a URL, and a run's record keeps `***` in its place. Then check the server through the tunnel,
which measures the round trip a segment will see, and run as on the laptop:

```bash
quackd policy check --policy-url http://127.0.0.1:9875 --bench
quackd run task.duck --robot arm-01-sim --policy-url http://127.0.0.1:9875 --llm openai
```

A tunnel that drops mid-segment ends the segment with the arm held, and `manipulate` fails,
saying why. The server's port is 9875 unless `--port` says otherwise, the one after the Jetson
daemon's 9874, and [SECURITY.md](../SECURITY.md) lists it with the rest.

## The policies

quackd's server serves three LeRobot policy types, ACT, SmolVLA and pi05, the three whose
processors it has read at LeRobot 0.6.1. Any other type is refused before a byte of its weights
is fetched.

### SmolVLA and ACT

**ACT is the laptop's policy.** It learns from your own demonstrations and nothing else, so it
knows your arm, your cameras and your task and no other, and it answers in chunks. An ACT
trained with temporal ensembling is asked every tick instead, and the server refuses one without
a GPU, because it has to answer inside a tick. On 2026-09-28 an ACT trained on an SO-101 and
published on the Hub, `natsuki0000/act-so101-bluecap` at commit
`82f75fe40a311026b4f7cacdea7bf14cadc44ccd`, answered a step in about two thirds of a second on
this project's laptop, an Intel Core i5-10210U with no GPU (the benches above), and drove a twin
of the lab's arm through two 10 s segments over `quackd serve-mcp`. On 2026-09-29 OpenAI's
`gpt-6-sol` flew that twin with `quackd run --goal` and `--policy-url`, handed the same ACT two
more and declared failure on seeing no blue cap, since the simulator's table holds a red cube and
a pen. Those runs prove the plumbing, and nothing about whether it would do its task on an arm.

**SmolVLA wants a GPU.** It is a small vision-language-action model, about 450M parameters by its
authors' count, and `lerobot/smolvla_base` is the base you fine-tune on your own episodes. It is
told the subtask in words, which is what `manipulate` passes it. Its backbone is a model it names
inside itself, so it needs `--pin`, as above, and it runs with any of the images it learned from
that no camera gives padded, which the run's record says. On the same laptop the same day,
`lerobot/smolvla_base` at commit `d9f33c94a60fb382c90dea2164c96845bd955e28`, its backbone pinned,
took 169 to 188 s for each chunk, timed in a script outside the server. 474 of its 500 parameter
tensors are bfloat16, which that CPU has no native arithmetic for, and cast to float32 in the
same script it still took about 18 s a chunk, past the 10 s the client waits for a step, so it
never returned a chunk through the client. Serve it on a rented GPU
([above](#on-a-rented-gpu)). Those are one run's numbers on one machine, kept as a record, and
nothing in quackd is set from them.

### Pi0.5

`pi05` needs LeRobot's `[pi]` extra besides the server's, and a Hugging Face token that has been
granted `google/paligemma-3b-pt-224`, the gated tokenizer it uses: accept its licence on the Hub,
then `hf auth login` on the machine that serves it. It is a GPU policy. A pi05 that learned
relative actions is served a whole chunk at a time, made absolute against the state it was
predicted from, where LeRobot's own loop makes each action absolute against the state of the
tick it is played at (`VLA_PIPELINE` in [the policies' table](adapters/lerobot.md#the-policies-upstream-lerobot-061)).
Its licence is unclear: see [the table](#licences).

### FLUX 3 Action

Black Forest Labs' action model has an official SO-101 checkpoint,
`black-forest-labs/flux-3-action-so101`. **quackd does not serve it, and has not run it
anywhere.** quackd's server refuses its policy type, and what would change that is a spike on a
rented Linux GPU that has not happened: that `quackd policy check --bench` holds the checkpoint's
rate through the tunnel, that its actions stay anchored when quackd clips a goal, and that the
official checkpoint drives the simulator end to end. PLAN.md carries it as an open item. What
running it takes, from Black Forest Labs'
[SO-101 guide](https://docs.bfl.ai/flux_3/flux3_action_so101) and
[inference page](https://docs.bfl.ai/flux_3/flux3_action_inference), and from
[LeRobot's FLUX 3 page](https://huggingface.co/docs/lerobot/main/en/flux3):

- **LeRobot from its main branch**, which is not on PyPI: from a checkout,
  `pip install -e ".[training,flux3,peft,diffusion]"`, then a NATTEN wheel built for that
  machine's torch and CUDA, from `whl.natten.org`.
- **Linux, Python 3.12 and an NVIDIA GPU.** The inference page says BF16 inference has been
  reported at about 32 GB of GPU memory, and that the peak depends on the checkpoint, the input
  resolution, the encoders and compilation.
- **A task LoRA on your own episodes.** The path its makers document for a new SO-101 task
  adapts the SO-101 checkpoint with a LoRA trained on your demonstrations, at 30 Hz, six
  commanded actions and six state values with the gripper last, from a scene camera and a wrist
  camera. Their example was trained on about 200 teleoperated demonstrations.
- **Its actions are deltas**, all but the gripper's, integrated against the last command every
  tick, and its percentiles are its training arm's calibration, which its makers say are not
  universal SO-101 values. quackd's check at connect would compare them with your arm's travel.

[Its card](https://huggingface.co/black-forest-labs/flux-3-action-so101) says what quackd's step
cap and guards are for: "Nothing in the model bounds joint velocity, force or workspace; the
application must enforce those limits and keep a hardware stop within reach."

## Licences

A policy's weights are your download, under their own licence, and quackd ships none of them.
LeRobot, which the server runs them in, is Apache-2.0. This table is what each one's own page
said on 2026-09-28, and it is not legal advice:

| Policy | Weights | What to know |
|---|---|---|
| ACT | yours: you trained it on your own data | nothing beyond your data's terms |
| SmolVLA ([`lerobot/smolvla_base`](https://huggingface.co/lerobot/smolvla_base)) | Apache-2.0, on its card | its backbone, [`HuggingFaceTB/SmolVLM2-500M-Video-Instruct`](https://huggingface.co/HuggingFaceTB/SmolVLM2-500M-Video-Instruct), is Apache-2.0 on its card too |
| Pi0.5 ([`lerobot/pi05_base`](https://huggingface.co/lerobot/pi05_base)) | the Hub card says `gemma`, and no licence file is shipped | [LeRobot's pi05 page](https://huggingface.co/docs/lerobot/pi05#license) says the model follows the Apache 2.0 licence of OpenPI, so the two disagree. Its tokenizer, [`google/paligemma-3b-pt-224`](https://huggingface.co/google/paligemma-3b-pt-224), is gated behind a licence you accept on the Hub |
| FLUX 3 Action ([`black-forest-labs/flux-3-action-so101`](https://huggingface.co/black-forest-labs/flux-3-action-so101)) | the [FLUX Kommunity License v1.0](https://huggingface.co/black-forest-labs/flux-3-action-so101/blob/main/LICENSE.md): non-commercial use, non-commercial Robotics Uses included | It calls any physical AI application a Robotics Use, control signals for any physical device included, and grants Robotics Uses for non-commercial purposes (Section 2.a). A Qualifying User, whose gross annualised revenue with its affiliates is under US$5,000,000, may also use the model's Outputs commercially, on the conditions of Section 2.e: content filtering or a review of Output, and saying Output was made with AI where the law requires it. The licence does not say that this reaches a Robotics Use, and anything it does not expressly authorise needs a licence from Black Forest Labs ([bfl.ai/licensing](https://bfl.ai/licensing)), so ask them before any commercial use on an arm. No output may be used to improve another model that does what a FLUX model does |

## Recording episodes, and training

quackd does not record, train or fine-tune, and it does not wrap LeRobot's tools that do. Record
with `lerobot-record`, a leader arm and the follower quackd drives, and train with
`lerobot-train`, as [LeRobot's own documentation](https://huggingface.co/docs/lerobot) describes.
How many episodes depends on the policy and the task. SmolVLA's documentation recommends about
50 as a starting point, and Black Forest Labs' SO-101 example used about 200, so 50 to 200 is
the range to plan for. Four things make a recording one quackd can serve on this arm:

- **Record on the calibration quackd drives with.** The arm's connect refuses a policy whose
  learned state lies outside this arm's calibrated travel, because a policy learned on an arm
  calibrated another way asks for goals that pin this one at its limits. Record with the same
  calibration id you give quackd, `arm-01` in the first-run guide. Calibrate the arm again and a
  policy learned on the old calibration may no longer fit it. `--accept-other-frame`, beside
  `--policy-url`, lets such a policy connect when you know the two arms' frames match. Every goal
  it answers is still clipped to this arm's travel, so the flag changes what drives the arm and
  never where the arm may go, and the run's record says it was given. A checkpoint from somebody
  else's arm is the case for it: on the lab arm's calibration, 55 of the 68 servable SO-100 and
  SO-101 ACT checkpoints on the Hub were refused, every one on `shoulder_lift`, whose recorded
  travel on the lab arm does not reach its own fold, while theirs did.
- **Name the cameras as the run will.** Each camera the arm has is the image of its own name,
  `observation.images.front` from the camera called `front`, unless `--cameras front=KEY` maps
  it. Record at the size the camera gives the run, or the connect refuses a frame of another
  size and says to give the camera that size with `--camera-url`'s `width=` and `height=`.
- **Serve it from the Hub.** `--policy` takes a Hub repository at a revision, so push the
  checkpoint LeRobot's training wrote to a repository of your own and serve it at the commit
  that push made.
- **Keep the dataset's rate.** The server reads the rate from the dataset the checkpoint names,
  at a commit or a tag, or you give it `--fps`.

## A checkpoint is code

Loading a LeRobot checkpoint imports whatever class its processor files name, a tokenizer step
can be asked to trust a repository's own code, and SmolVLA loads its backbone by name. That is
why no quackd command loads a checkpoint in the process that holds the serial bus, and why the
server reads before it builds:

- `config.json` and both processor files are fetched first, at the revision named, and a step
  named by a `class` key or by any name outside the ones ACT's, SmolVLA's and pi05's processors
  use is refused before any weights are fetched.
- A step that could trust a repository's code is told not to.
- A model the checkpoint names inside itself is refused unless `--pin` fixes it at a whole
  commit or a tag, and is fetched with no `.py` and no pickle. Its directory in the Hub's cache
  is refused if it holds any other kind of file, or a config that maps a class to code, with a
  sentence saying to point `HF_HUB_CACHE` at a fresh cache.
- Every weight is loaded strictly, or the server refuses to start.
- Every repository loaded, and the revision it was loaded at, is in `/v1/policy` and in the
  run's record.

None of that makes a checkpoint safe to serve because somebody sent it to you. Serve a
checkpoint at a revision you have read, from a Hub cache you control, on a machine that holds
nothing you would mind it reading. [SECURITY.md](../SECURITY.md) lists what would be a security
issue in the server itself.

One path in quackd skips all of it. `load_policy()` in the arm's backend is an older Python
helper that builds a LeRobot policy in the arm's own process, beside the serial bus, with none
of the reading above. Nothing in quackd calls it and no command reaches it, so it runs only if
your own Python calls it. The `LOAD_POLICY` row on
[the arm's page](adapters/lerobot.md#the-policies-upstream-lerobot-061) describes it, and
whether it goes is an open item
([ADR-0048](adr/0048-policies-are-the-arms-executor.md#consequences)).

## What a segment says

A segment's result says why it ended, how long it ran, the chunks, the goals clipped to the
travel and the ticks a second, and never that the subtask was done. This one is `scripted:sweep`
driving a twin of the arm, over MCP (`quackd serve-mcp --robot arm-01-sim --policy-url
http://127.0.0.1:9875 --yes --no-memory --no-log`), the `summary` `robot_run_verb` returned:

```
manipulate 'swing the wrist' ran 10 s and ended because its 10 s ran out (20 chunks, 0 goals clipped to the travel, 10 ticks a second). Nothing on the arm says whether it did the task: look at it before the next step
```

The ticks a second there are on the simulator's clock, which runs as fast as it steps, so they
say the loop paced the ticks it was asked for and nothing about the arm. Whether the subtask was
done is the pilot's to judge, from a fresh look at the arm, and with `--controller vla` it is
yours.
