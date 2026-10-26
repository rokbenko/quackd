# Policies: a learned policy as the arm's executor

On the SO-101 arm, `manipulate` and `pick` hand the arm to a learned policy for one short
segment. The pilot plans, one subtask at a time, and the policy executes: it reads the arm and
its cameras many times a second and answers with a goal for every motor, which quackd paces on
the arm's clock and holds to the arm's rules before each one is sent. The policy runs in a
server of its own, `quackd policy serve`, which you start, and a run reaches it with
`--policy-url`. This page is how to set that up, on the laptop beside the arm or on a rented
GPU, which policies it serves and under what licences, and where the data a policy learns from
comes from. What the verbs do and what refuses them is on
[the arm's page](../adapters/lerobot/README.md#manipulate-and-the-loop-a-policy-runs-in), and why it
is shaped this way is [ADR-0048](../adr/0048-policies-are-the-arms-executor.md).

> [!WARNING]
> **Nothing on this page has driven the arm.** Every segment so far has run against the test
> suite's fake arm and on the arm's simulator. On 2026-09-28 a trained ACT from the Hub,
> `natsuki0000/act-so101-bluecap` at commit `82f75fe40a311026b4f7cacdea7bf14cadc44ccd`, drove a
> twin of the lab's arm on the simulator through `quackd policy serve` and `quackd serve-mcp`,
> and on 2026-09-29 it drove the twin again under OpenAI's `gpt-6-sol` in a `quackd run --goal`.
> SmolVLA loaded on the same laptop and never answered a step in time on its CPU
> ([below](#smolvla-and-act)), pi05, pi0, GR00T N1.7 and a trained X-VLA have not run, and
> FLUX 3 Action is not served at all. Rehearse on the simulator first, keep a hand on the arm's
> power switch the first time a policy drives it, and send back what happened
> ([adapters/lerobot/first-run.md](../adapters/lerobot/first-run.md#14-what-to-report)).

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
Start on [the arm's simulator](../adapters/lerobot/README.md#the-simulator-lerobotmujoco) rather
than the arm, since a twin of your arm reaches a server exactly as the arm does.

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
([the calibration id](../adapters/lerobot/README.md#the-name-you-give-the-arm-is-its-calibration-id)).

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
on 2026-10-25. Its training config names its dataset at no revision, so it takes `--fps`, and the
rows about the policy itself are cut:

```
$ quackd policy check --policy natsuki0000/act-so101-bluecap@82f75fe40a311026b4f7cacdea7bf14cadc44ccd --fps 30 --bench
achieved    28.3 Hz of 30: 283 of the 300 ticks in 10.0 s sent an action
starved     0 ticks with nothing to send
skipped     17 ticks: the policy declares no --latency-s, so a chunk asked for with nothing left to
            play, its first among them, was waited for in the tick that asked for it, as a segment
            waits for one, and the ticks that passed meanwhile sent nothing: the arm would have
            held still through them
round trip  median 453.4 ms, p99 515.8 ms, max 515.8 ms over 6 requests
inference   median 428.9 ms on the server
latency     515.8 ms or less for 95% of the 7 steps timed, from the request to its chunk back:
            serve with --latency-s 0.52, so the simulator holds each chunk back as long, and bench
            again with it
```

A first bench is served with no `--latency-s`, and a segment waits for the first chunk of such a
policy in the tick that asked for it, as it waits for any chunk asked for with nothing left to
play. Every tick that passes while it waits is skipped, which on the arm is a tick that sends
nothing while the arm holds still, and here those are the 17 ticks the first chunk took to come
back. Every chunk after it was asked for with half the last one still queued, and landed while
the arm played that half. Skipped ticks are not counted as starved: a starved tick is one with a
chunk on its way and nothing left to play. The latency the bench suggests is what the simulator
holds each chunk back by, so that a rehearsal there plays what the arm would. **Bench it
again**, served with that latency and with the `--fps` the first bench needed:

```bash
quackd policy check --policy natsuki0000/act-so101-bluecap@82f75fe40a311026b4f7cacdea7bf14cadc44ccd --fps 30 --bench --latency-s 0.52
```

A good second bench skips no tick, starves only while its first chunk is on its way, which a
segment gives five seconds for, achieves close to the policy's rate, and says the `--latency-s`
it was served with covers what it timed. A step's time moves from bench to bench on a laptop's
CPU, and a bench over a few seconds times only a few steps, so the one here suggested more: its
second bench timed a step at 930.4 ms and said to serve with 0.94. Served with that, and given
`--seconds 30` for more steps to read the percentile from, it looked like this:

```
$ quackd policy check --policy natsuki0000/act-so101-bluecap@82f75fe40a311026b4f7cacdea7bf14cadc44ccd --fps 30 --latency-s 0.94 --bench --seconds 30
achieved    29.4 Hz of 30: 883 of the 900 ticks in 30.0 s sent an action
starved     17 ticks with nothing to send, waiting for a chunk to come back, every one before the
            first came back, which a segment gives time for
round trip  median 418.8 ms, p99 1052.2 ms, max 1052.2 ms over 22 requests
inference   median 403.6 ms on the server
latency     676.3 ms or less for 95% of the 23 steps timed, from the request to its chunk back: the
            --latency-s 0.94 it is served with covers that
```

A CPU that cannot keep up shows here, before an arm is involved, as starved ticks after the
first chunk, on either bench. Torch gets one thread fewer than it would take, which leaves a
core for the arm's process, and `--threads` says otherwise. A policy whose steps take longer
than a segment waits for its first chunk, or longer than half its chunk's actions, rounded
down, take to play, is given no `--latency-s` at all, since `serve` would refuse any that
covered them: the bench says it answers too slowly to drive an arm from that machine, and to
serve it on a GPU.

**Waiting for every chunk.** A run asks for each chunk while the arm still plays the last.
Given `--no-async-chunks` beside `--policy-url`, it plays each chunk out instead, holds the arm
still while the next is on its way and plays that from its first action, as LeRobot's own loop
plays chunks. That is slower by a round trip a chunk, and is there to compare against or to
fall back on. `quackd policy check --bench --no-async-chunks` paces a bench the same way, and
the run's record says which it was (`async_chunks`).

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
quackd policy serve --policy natsuki0000/act-so101-bluecap@82f75fe40a311026b4f7cacdea7bf14cadc44ccd --fps 30 --latency-s 0.94
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
([ADR-0045](../adr/0045-a-rest-pose-the-calibration-cannot-reach.md)) and past the model's stop as
well, so the twin starts with that joint settled just off the stop, clear of its table
([the arm's page](../adapters/lerobot/README.md#running-it)), and its first `manipulate` is refused.
Over MCP on 2026-09-28, with `scripted:sweep` served:

```
do refused: shoulder_lift reads -99.4, outside its calibrated travel of -84.2..84.2, so the policy was not started. A joint outside its travel is left out of every goal a policy sends, because the one goal its servo takes there is the end of its travel, and the policy could never move it. Move shoulder_lift inside its travel first
```

A pilot that can call `move_joints`, a model under `--controller llm` or an MCP client, moves the
joint inside first, as that run did, to `shoulder_lift` -40, `elbow_flex` 40 and `wrist_flex` 0,
and its two segments then ran. The same run made in-process ended with the rest move parking the
joint at the edge of its travel, in the pose the twin settled to there, and the close letting it
go. A move that takes the arm further from its fold can still end with the rest move setting the
gripper down on the table short of that pose, and the close then keeps torque on
([the arm's page](../adapters/lerobot/README.md#running-it)). `--controller vla` only ever calls
`manipulate`, so it cannot move the joint in, and its run ends in failure, in the verb's own
words. The lasting fix is to calibrate the arm again folded, so the fold lies inside
its travel, which is the bench item in [PLAN.md](../../PLAN.md) that reads the calibrated value at
each of the arm's stops.

A task file that means to use the policy allows `manipulate`, and a `duck: 3` file can list the
subtasks it may be told, how long each segment runs and how long they run in all
([reference/duck-spec.md](../reference/duck-spec.md#policy-v3)). A `--goal` run given `--policy-url`
allows `manipulate` too, and asks you before each segment when it runs at a terminal. `--yes`, or a
pipe or file on stdin, answers in your place, and nobody is asked. With `--controller vla` no
model is involved at all: a scripted pilot hands the policy each listed instruction in turn
and then asks you whether the arm did it ([the arm's page](../adapters/lerobot/README.md#a-scripted-pilot-that-a-person-judges---controller-vla)).

**What only the arm can tell you** is how fast the policy loop runs on the real bus while the
same laptop's CPU is inferring. Nothing timed on the simulator is a rate, because its clock is
lockstep. `check --bench` measures the server and the wire, and after a run on the arm the
`policy` block of its `summary.json` says what the loop achieved: its ticks, the late ones, the
ones with nothing to send, the ticks a second and the round trip.

## On a rented GPU

Pi0.5, Pi0, X-VLA, GR00T N1.7 and SmolVLA want a GPU. The server runs there, bound to that
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
daemon's 9874, and [SECURITY.md](../../SECURITY.md) lists it with the rest.

Hugging Face rents a GPU by the hour as well, with no machine of yours to set up and no tunnel,
and quackd starts the server on it for you ([below](#on-a-hugging-face-endpoint)).

## On a Hugging Face Endpoint

> [!WARNING]
> **quackd has never created a Hugging Face Endpoint: everything here ran against the test
> suite's stand-ins on 127.0.0.1 with a scripted policy behind them.** The stand-ins answer as
> Hugging Face's own pages say the API and its proxy do. What those pages leave out is in
> [the endpoint's table](../adapters/lerobot/README.md#the-endpoints-upstream-hugging-face-inference-endpoints),
> each row UNVERIFIED until a first real endpoint settles it, and no bench through one has been
> timed.

Instead of a GPU machine of your own, `quackd policy endpoint up` rents one from Hugging Face by
the hour, as an [Inference Endpoint](https://huggingface.co/docs/inference-endpoints), and runs
the same `quackd policy serve` on it, behind Hugging Face's TLS. The arm's process stays on the
laptop and reaches it with `--policy-url hf:NAME`, and the arm checks it before any torque as it
checks any server. Each time the endpoint starts, its container installs this release of quackd
from PyPI with `uvx`, so a build from a checkout cannot start one, and nothing uploaded after an
hour past the release is installed, so a start a month later resolves the same torch as the
first.

**What it needs.** A Hugging Face account with a way to pay, since an Inference Endpoint bills
it by the minute, and a token of that account that may create, pause and delete its endpoints,
which the API's own document marks as writes. Make it on huggingface.co, fine-grained where you
can, since what the token may do goes wherever it does. quackd finds it where huggingface_hub
does, in `HF_TOKEN`, then `HUGGING_FACE_HUB_TOKEN`, then the file `hf auth login` writes, and no
variable moves where it is sent. The laptop needs `quackd[lerobot]` and nothing more: the
container installs the server's extra itself.

**Start it**, with the flags the checkpoint needs, as `quackd policy serve` would take them:

```bash
quackd policy endpoint up arm-smolvla --policy lerobot/smolvla_base@d9f33c94a60fb382c90dea2164c96845bd955e28 --pin HuggingFaceTB/SmolVLM2-500M-Video-Instruct@7b375e1b73b11138ff12fe22c8f2822d8fe03467 --fps 30
```

Before anything bills, `up` checks what it can: that this quackd is a release on PyPI, that the
instance is a GPU Hugging Face offers in that region and its price that day, that the account has
quota left for one, that the checkpoint and every `--pin` exist at their revisions and are not
gated or private, and that the checkpoint's `config.json` is one the server serves. Every flag is
checked as `quackd policy serve` checks it, and a scripted policy is refused, since it needs no
GPU. Then it asks, and with no terminal to ask at it needs `--yes`. Here it is against the test
suite's stand-ins, run with `python tests/fake_hugging_face.py`, with the `y` a person types:

```
$ quackd policy endpoint up arm-smolvla --policy lerobot/smolvla_base@d9f33c94a60fb382c90dea2164c96845bd955e28 --pin HuggingFaceTB/SmolVLM2-500M-Video-Instruct@7b375e1b73b11138ff12fe22c8f2822d8fe03467 --fps 30
This starts a Hugging Face Inference Endpoint on rok's account: arm-smolvla, serving lerobot/smolvla_base@d9f33c94a60fb382c90dea2164c96845bd955e28 on nvidia-l4 x1 in aws us-east-1. It bills $0.80 an hour while it runs (Hugging Face's price, read just now), scales to zero after 15 minutes with no request, and bills nothing then or while paused. Start it? [y/N]: y
created  arm-smolvla on rok's account, nvidia-l4 x1 in aws us-east-1, $0.80 an hour while it runs
state    pending at 0 s
state    initializing at 1 s
state    running at 2 s
url      http://127.0.0.1:51814
token    kept in ~/.quackd/endpoints.json, for hf:arm-smolvla
serving  scripted:sweep (quackd-policy 1, quackd 0.26.0), gpu yes
  Run with --policy-url hf:arm-smolvla. quackd policy endpoint pause arm-smolvla stops the bill.
```

That is the stand-ins' output, and three of its rows would read otherwise on a real endpoint. Its
`url` is `https://<id>.endpoints.huggingface.cloud`, its states take minutes rather than seconds,
since the container installs torch and the checkpoint is fetched as it starts, and `serving`
names the checkpoint, where the stand-in container serves `scripted:sweep` in its place. `up`
waits 30 minutes unless `--wait-minutes` says otherwise, and returns at once with `--no-wait`. An
endpoint that fails, that does not serve in time, whose server says it has no GPU, or that meets
any other error while `up` waits is paused, which stops its bill, and the last lines of its log
are quoted, every token taken out of them.

**Check it and run on it**, as on any server, with no tunnel and no token to hand over:

```bash
quackd policy check --policy-url hf:arm-smolvla --bench
quackd run task.duck --robot arm-01-sim --policy-url hf:arm-smolvla --llm openai
```

Each reads the endpoint from Hugging Face's API first, one request with your token, and sends
that token to the endpoint only when the API says the endpoint by that name carries the tags `up`
gave it, at the address `up` recorded. A record left behind by an endpoint deleted on
huggingface.co is refused unless the API confirms it, and your token goes nowhere else. The
record is read only from a registry your shell or `--registry-dir` names, so a project's `.env`
cannot choose which endpoint drives your arm: a `QUACKD_REGISTRY_DIR` that came from one is
refused, saying so.

**Pause it** when you are done, which stops the bill until `up` resumes it, asking again first:

```bash
quackd policy endpoint pause arm-smolvla
```

A run that reaches a paused endpoint, or one still starting, is told so before the arm connects.
Against the stand-ins:

```
✗ error: hf:arm-smolvla is not serving (HTTP 503 from Hugging Face, not from quackd's server): it
is starting, paused or scaled to zero. quackd policy endpoint up arm-smolvla wakes it and waits
until it answers
```

`quackd policy endpoint list` says what this machine started and how each is now, and `delete`
removes one from Hugging Face and forgets it here. `up`, `pause` and `delete` each read the
endpoint first, and leave alone one by that name that does not carry the tag `up` gave yours, as
one made on huggingface.co after yours was deleted there, or by another machine's `up`, would
not: `delete NAME --forget` forgets such a record and asks Hugging Face nothing. An endpoint that
has no request for `--idle-minutes`, 15 unless told and from 15 to 720, scales to zero and bills
nothing, and the next request wakes it, which takes minutes: start it with `up` before a run
rather than waking it with one. `up` asks before it wakes one, as before it resumes a paused one.
quackd starts no endpoint that never scales to zero.

**What it costs.** Hugging Face's price list, `GET /v2/provider`, which `up` reads with no token
before it asks you, said this of AWS on 2026-10-25. An endpoint bills by the minute while it is
initializing or running, and nothing while it is paused or scaled to zero:

| GPU | `--instance` | Memory | Where (`--region`) | An hour |
|---|---|---|---|---|
| NVIDIA T4 | `nvidia-t4` | 16 GB | `us-east-1`, `eu-west-1` | $0.50 |
| NVIDIA L4, the default | `nvidia-l4` | 24 GB | `us-east-1` | $0.80 |
| NVIDIA A10G | `nvidia-a10g` | 24 GB | `us-east-1`, `eu-west-1` | $1.00 |

In Europe there was no L4 that day, so give `--region eu-west-1 --instance nvidia-a10g`.

**Where each token goes.** The endpoint is `authenticated`, which Hugging Face's configuration
page says any Hugging Face account can reach, so the lock on a policy whose answers move an arm
is the server's own token, which no account holds:

| Token | Where it comes from | Where it goes | Where it never goes |
|---|---|---|---|
| Your Hugging Face token | `HF_TOKEN`, `HUGGING_FACE_HUB_TOKEN` or `hf auth login`'s file | huggingface.co and api.endpoints.huggingface.cloud, to manage the endpoint, and as the bearer to the one endpoint address `up` recorded, once the API has said it still is that endpoint's | the endpoint, PyPI, the price list, any other address |
| The server's token | made by `up`, 64 hex digits | the endpoint's secrets as `QUACKD_POLICY_TOKEN`, and `~/.quackd/endpoints.json` | a URL, a run's record, the terminal |
| A Hub token for a gated checkpoint | the variable `--hub-token-from` names | the endpoint's secrets as `HF_TOKEN`, and huggingface.co, to read the checkpoint's config | refused when it is your account's own token |

**Gated and private checkpoints.** The endpoint fetches its checkpoint with a token of its own or
none, so `up` refuses a checkpoint or a `--pin` that is gated or private, pi05's PaliGemma
tokenizer among them, unless `--hub-token-from VAR` names a variable holding a read token granted
it. Make that a fine-grained read token for those repositories alone, since it goes into the
endpoint: your account's own token is refused there.

What a first real endpoint would settle, an ACT on a T4 for a few cents, is an open item in
[PLAN.md](../../PLAN.md), and why quackd starts a server for you here, where
[ADR-0048](../adr/0048-policies-are-the-arms-executor.md) has you start it, is
[ADR-0061](../adr/0061-a-hugging-face-endpoint-is-a-rented-gpu-quackd-starts.md).

## The policies

quackd's server serves six LeRobot policy types, ACT, SmolVLA, pi05, pi0, X-VLA and GR00T
N1.7, each allowed the processor steps read for it at LeRobot 0.6.1 and no other type's, an
X-VLA in its `auto` action mode alone ([below](#x-vla)) and a GR00T whose every action group is
joints ([below](#gr00t-n17)). Any other type is refused before a byte of its weights is fetched,
and pi0-FAST in a sentence of its own ([below](#pi0-and-why-not-pi0-fast)). MolmoAct2 is not
served yet ([below](#molmoact2)).

### SmolVLA and ACT

**ACT is the laptop's policy.** It learns from your own demonstrations and nothing else, so it
knows your arm, your cameras and your task and no other, and it answers in chunks. An ACT
trained with temporal ensembling is asked every tick instead, and the server refuses one without
a GPU, because it has to answer inside a tick. On 2026-09-28 an ACT trained on an SO-101 and
published on the Hub, `natsuki0000/act-so101-bluecap` at commit
`82f75fe40a311026b4f7cacdea7bf14cadc44ccd`, answered a step in about two thirds of a second on
this project's laptop, an Intel Core i5-10210U with no GPU, and drove a twin of the lab's arm
through two 10 s segments over `quackd serve-mcp`. On 2026-09-29 OpenAI's `gpt-6-sol` flew that
twin with `quackd run --goal` and `--policy-url`, handed the same ACT two more and declared
failure on seeing no blue cap, since the simulator's table holds a red cube and a pen. Those runs
prove the plumbing, and nothing about whether it would do its task on an arm. On 2026-10-25 the
same ACT answered a step there in about half a second (the benches above).

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

`pi05` needs a Hugging Face token that has been granted `google/paligemma-3b-pt-224`, the gated
tokenizer it uses: accept its licence on the Hub, then `hf auth login` on the machine that serves
it. The server's extra brings LeRobot's `[pi]` with it, and the server fetches the tokenizer's
files alone, 21.9 MB, and none of PaliGemma's 11.69 GB of weights, both read off the Hub on
2026-10-25. It is a GPU policy. A pi05 that learned relative actions is served a whole chunk at
a time, made absolute against the state it was predicted from, where LeRobot's own loop makes
each action absolute against the state of the tick it is played at (`VLA_PIPELINE` in
[the policies' table](../adapters/lerobot/README.md#the-policies-upstream-lerobot-061)).
Its licence is unclear: see [the table](#licences).

### Pi0, and why not Pi0-FAST

`pi0` is Physical Intelligence's flow-matching VLA from before pi05, in LeRobot's port. To the
server it is a pi05 with one step changed: its processors end the task with a newline for
PaliGemma's tokenizer where pi05's write the arm's state into the prompt. So it needs the same
Hugging Face token granted `google/paligemma-3b-pt-224`, the same `--pin` for it, and the same
GPU. Its chunks are 50 actions, all of them played, by LeRobot's defaults. A pi0 that learned
relative actions is served a whole chunk at a time, made absolute against the state it was
predicted from, as a pi05's are, and it runs with any camera it is not given padded. Its own
loader hands back a random network when its weights do not load, as pi05's does, so the server
loads them itself, strictly (`PI0_FROM_PRETRAINED` in
[the policies' table](../adapters/lerobot/README.md#the-policies-upstream-lerobot-061)).

**Pi0 has not run anywhere.** quackd has not loaded, benched or served one, on this project's
laptop or on a GPU. Its checks were written against the SO-101 fine-tunes on the Hub on
2026-10-25: `Harrysunshine/so101-pi0-real-9task` at commit
`e2bfbfaec63723db4ede582e07ff27e2cf1eb436` and `haydendoo/so101_pickup_pi0_medium_new_v1` at
`d03778fefc1a0605b94233770b11d19671090aaa` each name six motors in the bus's order, a top and a
wrist camera, chunks of 50 and PaliGemma's tokenizer. Each names the dataset it learned from at
no revision, so each takes `--fps`. Each one's `model.safetensors` was 8.89 GB that day, and the
tokenizer files the server fetches beside it 21.9 MB.

**Pi0-FAST is refused.** `pi0_fast` turns its answer into actions with the FAST tokenizer, and
its policy loads that tokenizer with `trust_remote_code=True`, hard-wired where no override
reaches (`PI0_FAST_REMOTE_CODE`), so serving one would run the Python its tokenizer's repository
brings with it. The server refuses the type on its `config.json`, before anything else of the
checkpoint is fetched. An SO-101 fine-tune on the Hub, checked in LeRobot 0.6.1's environment on
2026-10-25, with its 2,120-byte `config.json` the one file fetched:

```
✗ error: kominetti/pi0fast-so101-pickup-place-v2@2d74f0b0cb9cc7b2cc96330293c4b6c4daa7d349 is a
'pi0_fast' policy, which turns its answer into actions with the FAST tokenizer's own Python code
from the Hub, loaded with trust_remote_code=True inside the policy, and quackd runs no code a
repository brings with it: serve a pi0 or pi05 instead (upstream_api.PI0_FAST_REMOTE_CODE)
```

What would change that is LeRobot loading the FAST tokenizer without its repository's code,
which [PLAN.md](../../PLAN.md) carries.

### X-VLA

`xvla` is X-VLA, Jinliang Zheng and his co-authors' soft-prompted VLA, in LeRobot's port. Its
Florence-2 is built from its own `config.json` and never fetched, so the one model it names is
BART's tokenizer, `facebook/bart-large`, which is not gated. Pin it with
`--pin facebook/bart-large@REVISION`, and the server fetches its tokenizer files, 2.7 MB on
2026-10-25, since BART publishes no safetensors at all. What its answer means is set by
`action_mode` in its config, and quackd serves `auto` alone: the dataset's own action, which for
an SO-101's data is the arm's own joint positions. `ee6d`, the default, and `agibot_ee6d` are an
end effector's pose, and `joint`, `franka_joint7` and `so101_bimanual` other robots' joints, so
each is refused before a pin or a weight, and so is LIBERO's post-processor step,
`xvla_rotation_6d_to_axis_angle`, which turns an end-effector rotation into axis-angle and the
gripper into -1 or 1 (`XVLA_ACTION_MODES` and `XVLA_LIBERO_ROTATION` in
[the policies' table](../adapters/lerobot/README.md#the-policies-upstream-lerobot-061)). Its
domain step hands the model the domain its data came from, which has to be one of the model's
own. Its chunks are 32 actions by LeRobot's default and 30 in the SO-101 fine-tunes, it runs with
a camera it is not given padded, and its own loader is strict.

**No trained X-VLA has run anywhere.** quackd has not loaded, benched or served one, on this
project's laptop or on a GPU. CI's `policy` job serves a tiny random one, offline, through the
real server and client: a Florence-2 a layer or two deep, built from its config, and a tokenizer
of a few words pinned at a commit in a Hub cache of its own, which proves the plumbing and
nothing about what a trained one does. Its checks were written against
`mertkirgil/xvla-so101-renkli-kalem` at commit `7498e90c61112a03ba7640199a8a363e6c34f276`, in
`auto` mode with six motors and a front camera, whose `model.safetensors` was 1.76 GB on
2026-10-25. Several SO-101 X-VLAs on the Hub keep the eight-number state of `lerobot/xvla-base`,
which they were fine-tuned from, `fabono/xvla_so101_black_quad` at
`78a2bb71d3dd95287652a392b45c9e496ce4ef7a` among them. The server serves one of those, and the
arm's connect refuses it, since its state is not as long as an SO-101 has motors. Others name
their tokenizer by a path on the machine that trained them, which the server refuses, since it
loads nothing by a path a checkpoint wrote. Here is the base itself, in `ee6d`, checked in
LeRobot 0.6.1's environment on 2026-10-25, which fetched its `config.json` and its two processor
files and none of its 3.52 GB of weights:

```
✗ error: lerobot/xvla-base@cdb7964e4fe842935d671bfab5a5ebe00a96648c is an X-VLA whose action_mode
is 'ee6d', which is not this arm's joints, and quackd serves an X-VLA in 'auto' mode
(upstream_api.XVLA_ACTION_MODES)
```

### GR00T N1.7

`groot` is NVIDIA's GR00T N1.7, in LeRobot's port, built on the Cosmos-Reason2-2B backbone. A
checkpoint names two models inside itself, and both are pinned with `--pin`. One is the base it
was fine-tuned from, `nvidia/GR00T-N1.7-3B`, its `base_model_path`, which the policy is built
from, so the server fetches it with its weights. The other is the backbone's tokenizer and
processors, `nvidia/Cosmos-Reason2-2B`, fetched as its tokenizer files alone. Cosmos is gated:
accept NVIDIA's licence on the Hub, and serve with a token that has been granted it. A config
that names no base is refused, since LeRobot would fill in its own at no revision. The base's
own `config.json` is read before any weight, because LeRobot builds the backbone from a config
it carries only while the base names Cosmos-Reason2-2B as its backbone, and fetches any other
trusting its repository's code. LeRobot loads Cosmos's tokenizer with `trust_remote_code=True`,
hard-wired, and the pin leaves it nothing of the repository's own to trust: no `.py` is
fetched, and a directory whose configs map a class to code is refused
(`GROOT_BASE_MODEL` and `GROOT_VLM_PROCESSOR` in
[the policies' table](../adapters/lerobot/README.md#the-policies-upstream-lerobot-061)).

**Joints, and nothing else.** A GR00T decodes its answer group by group, each unnormalised by
its own statistics, and the SO-101 fine-tunes on the Hub come in two kinds. One learned its arm
joints relative to the state, with the gripper absolute, and decodes by statistics its
processors keep grouped: its chunk is made absolute a whole chunk at a time, against the state
it was predicted from, which is how LeRobot itself decodes one. The other learned absolute
actions and decodes by its dataset's statistics. Both are served. A group decoded as an end
effector's pose (EEF) is refused, and so is LIBERO's gripper, which the decode turns into -1 or
1, a decode with no statistics, whose actions would reach the arm still normalised to -1..1, and
any GR00T N1.5 checkpoint, which LeRobot 0.6.1 no longer serves. Its chunks are 40 actions by
LeRobot's default, and 16, 32 or 40 in the SO-101 fine-tunes read on the Hub on 2026-10-25.

**Every camera, and its frame.** A GR00T pads no camera it is not given: it would see fewer
views than it learned on, so the arm needs a camera for every image it looks at, and a
checkpoint whose processors look for a camera its config has no image for is refused. The
percentiles of the state it learned from are kept where LeRobot's normaliser keeps none, in its
processors' grouped statistics, so the server reads them from there, group by group, and the
arm's frame check is made on them as on any other policy's (`GROOT_PERCENTILE_STATS`).

**GR00T N1.7 has not run anywhere.** quackd has not loaded, benched or served one, on this
project's laptop or on a GPU. Its checks were written against two SO-101 fine-tunes on the Hub,
read on 2026-10-25: `jammyyang/groot17_so101_pick_place` at commit
`36d374074e5604ed1348cccf10ab64cd1996b393`, relative with the gripper absolute, and
`haydendoo/so101_pickup_groot_medium_new_v1` at `41eaaf5180c610f07bb8d5cd141b9b05d55af58c`,
absolute. Their `model.safetensors` were 9.34 GB and 12.58 GB that day, and the base's weights
6.91 GB beside them, with Cosmos's tokenizer files 11.5 MB, so serving one fetches about 16 to
20 GB. Its backbone's processors are built the first time it is asked for a chunk, so a
session's first chunk takes longer than the rest, which the 5 s a segment gives its first chunk
may not cover. NVIDIA's own LIBERO checkpoint, checked in LeRobot 0.6.1's environment on
2026-10-25, fetched its `config.json` and its two processor files, 7,212 bytes, and nothing
else:

```
✗ error: nvidia/gr00t17-lerobot-libero_10-640@5ee08ab09fac5c5ef2388a14c882ea825ac861db decodes its
gripper with LIBERO's transform, to -1 or 1, and an SO-101's gripper takes 0..100: serve a GR00T
fine-tuned on this arm's own data (upstream_api.GROOT_DECODE)
```

An N1.5 fine-tune is refused on its pre-processor's pack step, with 3,333 bytes fetched:

```
✗ error: step 2 of
zhoumiaosen/groot-n1p5-lora-libero10-gray@5c3bb1d8d272cbb5ba5429ebeb75ff3e24da813c's
policy_preprocessor.json is 'groot_pack_inputs_v3', which only GR00T N1.5's processors name, and
LeRobot 0.6.1 serves GR00T N1.7 alone: serve a GR00T fine-tuned on N1.7
(upstream_api.GROOT_N1_5_REMOVED)
```

### MolmoAct2

Ai2's MolmoAct2 is not served yet. Its base model maps its classes to code of its own
(`auto_map`), which the server refuses in every model it loads, and serving it would take the
first exemption from that rule, which was deferred rather than written
([ADR-0060](../adr/0060-hugging-face-serves-many-vlas.md)).

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
said on 2026-09-28, and the Pi0, X-VLA and GR00T N1.7 rows' on 2026-10-25, and it is not legal
advice:

| Policy | Weights | What to know |
|---|---|---|
| ACT | yours: you trained it on your own data | nothing beyond your data's terms |
| SmolVLA ([`lerobot/smolvla_base`](https://huggingface.co/lerobot/smolvla_base)) | Apache-2.0, on its card | its backbone, [`HuggingFaceTB/SmolVLM2-500M-Video-Instruct`](https://huggingface.co/HuggingFaceTB/SmolVLM2-500M-Video-Instruct), is Apache-2.0 on its card too |
| Pi0.5 ([`lerobot/pi05_base`](https://huggingface.co/lerobot/pi05_base)) | the Hub card says `gemma`, and no licence file is shipped | [LeRobot's pi05 page](https://huggingface.co/docs/lerobot/pi05#license) says the model follows the Apache 2.0 licence of OpenPI, so the two disagree. Its tokenizer, [`google/paligemma-3b-pt-224`](https://huggingface.co/google/paligemma-3b-pt-224), is gated behind a licence you accept on the Hub |
| Pi0 ([`lerobot/pi0_base`](https://huggingface.co/lerobot/pi0_base)) | the Hub card says `gemma`, and no licence file is shipped | [LeRobot's pi0 page](https://huggingface.co/docs/lerobot/pi0#license) says the model follows the Apache 2.0 licence of OpenPI, so the two disagree, as pi05's do. Its tokenizer is pi05's, gated the same way |
| X-VLA ([`lerobot/xvla-base`](https://huggingface.co/lerobot/xvla-base)) | Apache-2.0, on its card | its tokenizer, [`facebook/bart-large`](https://huggingface.co/facebook/bart-large), is Apache-2.0 on its card, and not gated |
| GR00T N1.7 ([`nvidia/GR00T-N1.7-3B`](https://huggingface.co/nvidia/GR00T-N1.7-3B)) | the [NVIDIA Open Model License Agreement](https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-open-model-license/), in its card's own words, though the repository carries no licence tag | its backbone's tokenizer, [`nvidia/Cosmos-Reason2-2B`](https://huggingface.co/nvidia/Cosmos-Reason2-2B), is under the same licence and gated behind it: you accept it on the Hub. [LeRobot's GR00T page](https://huggingface.co/docs/lerobot/groot#license) says N1.7 is released under it too |
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
  named by a `class` key, or by any name outside the ones the checkpoint's own type uses, is
  refused before any weights are fetched. Each type is allowed the steps every policy is built
  from and the ones its own processors add, read at LeRobot 0.6.1, and no other type's, so an
  ACT that names a pi05's tokenizer is refused.
- A step that could trust a repository's code is told not to.
- A model the checkpoint names inside itself is refused unless `--pin` fixes it at a whole
  commit or a tag, and is fetched with no `.py` and no pickle. A tokenizer, which is what a
  processor step names, comes without its weights, and a backbone the checkpoint's config names
  comes with them. Its directory in the Hub's cache is refused if it holds any other kind of
  file, or a config that maps a class to code, with a sentence saying to point `HF_HUB_CACHE` at
  a fresh cache. One that holds a PEFT adapter is refused too, since with peft installed,
  which LeRobot's own `[peft]` and `[groot]` extras bring, transformers would load the
  adapter onto the model.
- Every weight is loaded strictly, or the server refuses to start.
- Every repository loaded, and the revision it was loaded at, is in `/v1/policy` and in the
  run's record.

None of that makes a checkpoint safe to serve because somebody sent it to you. Serve a
checkpoint at a revision you have read, from a Hub cache you control, on a machine that holds
nothing you would mind it reading. [SECURITY.md](../../SECURITY.md) lists what would be a security
issue in the server itself.

One path in quackd skips all of it. `load_policy()` in the arm's backend is an older Python
helper that builds a LeRobot policy in the arm's own process, beside the serial bus, with none
of the reading above. Nothing in quackd calls it and no command reaches it, so it runs only if
your own Python calls it. The `LOAD_POLICY` row on
[the arm's page](../adapters/lerobot/README.md#the-policies-upstream-lerobot-061) describes it, and
whether it goes is an open item
([ADR-0048](../adr/0048-policies-are-the-arms-executor.md#consequences)).

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
