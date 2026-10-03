# Decision LLMs: a discrete stepper in front of the model

quackd asks one question a turn -- *which single tool call now* -- and pays a frontier model's
full latency for it whether the answer is `report_state` or a six-joint pose. On the SO-101 run at
the top of [README.md](../README.md) that is **62.1 seconds of a 78.8 second run** spent waiting
on the model, against 12.2 seconds of the arm actually moving.

Some of those turns are not writing. They are choosing.

A **decision LLM** is what answers those. You send it a named state and typed questions, and it
answers with a value and a probability distribution. No text generation, no parsing, nothing to
coerce into JSON. [TypeSafe's **Jev**](https://docs.typesafe.ai/introduction) was the first and
gave the wire format its name -- *System One* -- and there are several now:
**[Kev](decision-llms/kev.md)**, **[Von](decision-llms/von.md)**,
**[OpenJev](decision-llms/openjev.md)** and **[OpenDecision](decision-llms/opendecision.md)** are
servers you run yourself, **[Laya](decision-llms/laya.md)** loads into this process, and anything
else that speaks the same `POST /v1/systemone` is reached with `--decision-url`
([local](decision-llms/local.md)). quackd treats them as one seam with a row of data in front of
it, so most of this page is about the half that does not change with the vendor.

**A decision LLM is not another LLM, and it is not a smaller one.** That is the whole point of it,
so it is worth being precise about the difference before any of the rest makes sense. A language
model generates tokens: you ask it for a verb and it writes one, and everything quackd does around
that -- the tool schemas, the one-call-per-turn rule, the re-prompt when it answers with prose --
exists to squeeze a text generator into a shape software can branch on. A decision LLM generates
nothing. It scores a fixed set of options against a state and hands back which one, with a
probability for each. In TypeSafe's own words, *"LLMs produce words for people. Jev produces typed
decisions"*, and it is *"more like code: reliable, fast, self-consistent, and type-safe"*.

| | A language model | A decision LLM |
|---|---|---|
| Output | tokens, which you parse | a typed value, already a value |
| Can it invent an option? | yes, and does | no. It can only score the ones you gave it |
| Can it write a joint angle? | yes | **no**, and not by rule: there is nowhere in the answer for a number to come from |
| Can it be used as a chatbot? | it is one | no, at all |
| What uncertainty looks like | a hedge in prose, or none | a number, per option, which your code reads |
| Where it sits in quackd | the pilot | in front of the pilot, for the turns that are a choice |

The practical consequence is the one this page is about. A frontier model takes about six seconds
to say `report_state` on this arm, because it takes about six seconds to say anything. A
classifier answers a six-way choice in a fraction of one, and charges for the question rather than
for the essay.

`--decision-llm` is **off by default**, and stays off unless you name one. quackd installs no
decision LLM, needs no key for one, and never switches this on because it found a key in your
`.env`.

> [!IMPORTANT]
> The stepper cannot author a number and cannot author a sentence. Every joint angle, every
> velocity, every `reason` and every feasibility verdict is still the model's, on every body. What
> it can do is pick one of a handful of calls this robot already has.

> [!WARNING]
> **Nobody has run any of this against a real robot.** Every decision LLM on this page has been
> exercised against the mock arm, the simulators and the test suite with a fake standing in for
> the server, and not one of them has driven hardware. That is the same standing as the rest pose
> and `--by-hand` ([README](../README.md#which-robots-work)).

**Contents**

- [What it is, and what it is not](#what-it-is-and-what-it-is-not)
- [The ones quackd names](#the-ones-quackd-names)
  * A page each: [jev](decision-llms/jev.md) · [kev](decision-llms/kev.md) ·
    [von](decision-llms/von.md) · [openjev](decision-llms/openjev.md) ·
    [opendecision](decision-llms/opendecision.md) · [local](decision-llms/local.md) ·
    [laya](decision-llms/laya.md)
- [What is not covered, and why](#what-is-not-covered-and-why)
- [Install and switch on](#install-and-switch-on)
- [The SO-101 arm, in four parts](#the-so-101-arm-in-four-parts)
  * [1. What the arm can and cannot hand over](#1-what-the-arm-can-and-cannot-hand-over)
  * [2. The hero run, call by call](#2-the-hero-run-call-by-call)
  * [3. Where the arm does hand over: the grip
    check](#3-where-the-arm-does-hand-over-the-grip-check)
  * [4. The other six bodies](#4-the-other-six-bodies)
- [How much faster, and how much cheaper](#how-much-faster-and-how-much-cheaper)
- [How a turn is decided](#how-a-turn-is-decided)
- [What it is never allowed to do](#what-it-is-never-allowed-to-do)
- [What the record says](#what-the-record-says)
- [Measuring it yourself](#measuring-it-yourself)
- [Writing a plugin](#writing-a-plugin)
- [Configuration](#configuration)
- [Limitations](#limitations)

## What it is, and what it is not

| | |
|---|---|
| **It is** | a classifier that answers typed questions about a state: which of these calls, is this statement true, where on this rubric |
| **It is not** | a provider. `--llm` does not take it, `quackd list-models` does not list it, and it cannot pilot a robot on its own |
| **It decides** | which single verb, out of the ones this body's contract allows *and* whose parameters are a closed set |
| **The model decides** | every pose, every target, every sentence, every feasibility verdict, every `declare_success`, and every turn the stepper is not confident about |
| **The executor still decides** | whether the verb runs at all: the allowlist, the budgets, the confirm gates, the preconditions and the robot's own safety authority are untouched |

The division is the same one TypeSafe's own [smart-home
demo](https://docs.typesafe.ai/demos/smart-home) draws: the classifier picks the device and the
action, and the language model does the language. Here the classifier picks the verb and the model
does the angles and the prose.

The two seams are genuinely separate, and it is worth saying so plainly now that the flags look
alike. `--llm` names the pilot, which is a provider and a model: `--llm openai:gpt-5`.
`--decision-llm` names the thing in front of it: `--decision-llm kev`. Neither list contains the
other, and a run that names no decision LLM is quackd exactly as it has always been.

## The ones quackd names

Every row below is data in
[`quackd/agent/decision/catalogue.py`](../quackd/agent/decision/catalogue.py) and nothing else --
a name, a summary, an install line, an address, a model id, a key variable and a rate. Adding a
wire-compatible server is one row, one page and no code, which is why the escape hatch near the
bottom of the table exists for the ones quackd has never heard of.

| Name | Who makes it | Where it runs | How you get it running | Key | Default URL | Default model | Page |
|---|---|---|---|---|---|---|---|
| `jev` | TypeSafe ([typesafe.ai](https://typesafe.ai/)) | their machines, hosted | `quackd[decision] and TYPESAFE_API_KEY (typesafe.ai)` | `TYPESAFE_API_KEY` | the SDK's own | `jev-1.13.0` | [jev](decision-llms/jev.md) |
| `kev` | [jaredpalmer/kev](https://github.com/jaredpalmer/kev) | your own GPU | `git clone https://github.com/jaredpalmer/kev && cd kev && uv sync --extra serve && KEV_DTYPE=bf16 uv run --extra serve python -m kev.serve --run jaredpalmer/kev-4b --port 8009` | none needed | `http://127.0.0.1:8009` | `kev-latest` | [kev](decision-llms/kev.md) |
| `von` | `von-sdk` on PyPI | your own machine, CPU included | `pip install von-sdk && von serve --host 127.0.0.1 --port 8000` | none needed | `http://127.0.0.1:8000` | `von-latest` | [von](decision-llms/von.md) |
| `openjev` | `razorback16/openjev` | your own GPU, or MLX on Apple silicon | `docker run -d --gpus all --ipc=host -p 127.0.0.1:8080:8080 -v ~/.cache/huggingface:/root/.cache/huggingface razorback16/openjev:0.3.0`, or `OPENJEV_BACKEND=mlx python -m openjev` on Apple silicon | none needed | `http://127.0.0.1:8080` | `openjev-latest` | [openjev](decision-llms/openjev.md) |
| `opendecision` | `OpenDecision` on PyPI | your own machine, no GPU needed | `pip install OpenDecision && opendecision serve` | none needed | `http://127.0.0.1:8000` | `opendecision` | [opendecision](decision-llms/opendecision.md) |
| `local` | whoever wrote your server | wherever you put it | `--decision-url http://host:port (or QUACKD_DECISION_URL)` | none needed | you have to say | the server names its own | [local](decision-llms/local.md) |
| `laya` | an encoder off Hugging Face | **in this process**, no server at all | `uv pip install "quackd[laya]"`, which pulls torch. Its weights download on first use | none needed | none at all | `typed-decisions` | [laya](decision-llms/laya.md) |

**How you get it running** is a command you can paste on five of the seven rows, and the catalogue
row holds exactly that command with nothing else inside it: where a cell here adds a clause, as
`openjev`'s does for Apple silicon and `laya`'s does for what it pulls, the clause is the table's
and starts after the code span ends. The two rows that are not commands say so by being
unrunnable: `jev` names an extra and a key you have to get from a vendor, and `local` names the
flag that tells quackd where your server already is. Each page carries the same line and the
sentence around it.

Two more columns are worth reading twice. **Key** is `none needed` on every row but the hosted
one, and that is structural rather than a convenience: a server you run yourself is sent the
literal word `local` in the key field, so a `TYPESAFE_API_KEY` sitting in the same `.env` is never
posted to a port on your own machine. **Default model** is per row rather than shared, and
[openjev](decision-llms/openjev.md) is what happens to anyone who assumes otherwise: its accepted
ids are a closed set, and a pinned Jev version is refused with a 400 on every request.

Six of the seven rows speak the same wire format over the same client, which is why a server
quackd has never heard of costs no code at all. `laya` is the seventh and the proof that the seam
here is a protocol rather than an HTTP call: the same four questions go to it unchanged, and it
has no address to send them to.

> [!WARNING]
> **Two of the confidence floors on this page are numbers TypeSafe publish, and two are
> quackd's own.** Every row here inherits all four unmeasured, and each server computes its
> confidence differently: a 4B decoder with
> a decision head, a 395M encoder and a zero-shot classifier do not mean the same thing by 0.87.
> `--decision-mode shadow` before `--decision-mode on` is the rule and not a suggestion, and it is
> the rule on every row, the one the published half came from included.

`quackd doctor` prints the same table against your own machine, saying what is installed and what
is not:

```
discrete stepper: decision LLMs (quackd run --decision-llm; off unless you name one) --------------
+-------------------------------------------------------------------------------------------------+
| decision llm | extra              | key                | model              | url               |
|--------------+--------------------+--------------------+--------------------+-------------------|
| jev          | missing            | TYPESAFE_API_KEY   | jev-1.13.0         |                   |
|              | (quackd[decision]) | unset              |                    |                   |
| kev          | missing            | none needed        | kev-latest         | http://127.0.0.1: |
|              | (quackd[decision]) |                    |                    | 8009              |
| von          | missing            | none needed        | von-latest         | http://127.0.0.1: |
|              | (quackd[decision]) |                    |                    | 8000              |
| openjev      | missing            | none needed        | openjev-latest     | http://127.0.0.1: |
|              | (quackd[decision]) |                    |                    | 8080              |
| opendecision | missing            | none needed        | opendecision       | http://127.0.0.1: |
|              | (quackd[decision]) |                    |                    | 8000              |
| local        | missing            | none needed        | auto (the server   | unset:            |
|              | (quackd[decision]) |                    | names it)          | --decision-url or |
|              |                    |                    |                    | QUACKD_DECISION_U |
|              |                    |                    |                    | RL                |
| laya         | missing            | none needed        | typed-decisions    | in this process   |
|              | (quackd[laya])     |                    |                    |                   |
+-------------------------------------------------------------------------------------------------+
One of these answers the turns that are a choice among calls this body can make. Every pose, every
sentence and every verdict is still the model's (docs/decision-llms.md).
```

> [!NOTE]
> **None of the install or serve commands on the pages under `docs/decision-llms/` has been run
> here.** They are transcribed from each project's own README and source, read on 2026-09-22, and
> each page marks the lines it could not run. No output is pasted for any of them, because
> inventing a line a server printed would be the one kind of mistake these pages cannot afford.
> The `quackd` commands on this page were run, and say so where it matters.

Each row has a page under [`docs/decision-llms/`](decision-llms/), in the shape of the robot pages
under [`docs/adapters/`](adapters/): the row it has to agree with, what was read from that
project's README and source and on what date, what quackd assumes about it and what it does about
each assumption, and the word *never* until one of them has answered a real robot. The **Page**
column is the way in, and a test reads every row back off its page.

## What is not covered, and why

This section is the honest half of the table above. Four things look like they belong in it and do
not, and saying why is more useful than leaving a reader to find out.

**SemIf** ([TheoLeeCJ/SemIf](https://github.com/TheoLeeCJ/SemIf)) is a batch CLI over a JSONL
file. It is choice-only, it has an input schema of its own, and it has no server at all: you hand
it a file and it hands you a file. quackd asks one question about one state per turn and reads the
answer inside a one-second timeout, so a batch tool over a file is the wrong shape rather than a
missing row. Someone who wants it can wrap it in a System One shim and reach that with
`--decision-llm local`, or write a plugin; either way the work is the adapter, and quackd needs no
change for either.

**OpenRouter's Decisions API** is a different path. It answers at `/api/alpha/decisions`, and the
System One client's path is a fixed constant rather than something `--decision-url` can reach
into. A row for it would be a second transport, not a second address.

**Ollama** runs chat models. None of the architectures above runs in it, and its OpenAI-compatible
endpoint drops logprobs, so even the closest thing you could build -- a small chat model scored by
token probability -- has nothing to read the probability off. Ollama is excellent at the other
seam: `--llm ollama:llama3.2` is a pilot, and that is [local-llms.md](local-llms.md).

**A plain LLM behind a System One adapter** is the one people ask about most, and the one with the
sharpest objection. You can absolutely prompt a model for a label and a number between 0 and 1,
and it will give you both. But that number is a *prompted* number rather than a *calibrated* one:
it is what a model writes when asked how sure it is, not a statement about how a population of
answers at that value behaves. quackd's floors gate real motion on calibration -- 0.85 to send an
intent, 0.90 behind a confirm gate -- and a prompted 0.87 and a calibrated 0.87 are not the same
claim. Someone could ship exactly this as a plugin, and it would be a genuinely useful thing to
have. They would have to measure it first, which is what `--decision-mode shadow` is for.

## Install and switch on

```bash
uv pip install "quackd[decision]"                  # the System One client, for every server row
uv pip install "quackd[laya]"                      # the one that runs in this process
uv pip install "quackd[lerobot,decision,openai]"   # the arm, a decision LLM and a model
```

Neither extra is part of `quackd[all]`, for the same reason the simulators are not: `laya` pulls
torch, and nobody should pay for a vendor that is not in their run.

Three flags, because there are many decision LLMs now rather than one:

| Flag | What it says | Default |
|---|---|---|
| `--decision-llm NAME[:MODEL]` | which one answers, and optionally which model id it answers as | none, and then none of the rest of this happens |
| `--decision-url URL` | where your own server listens. No path: the client adds `/v1/systemone` | whatever the row says |
| `--decision-mode off\|shadow\|on` | what its answer is allowed to do | `on` once you have named one, `off` when you have not |

**Naming a decision LLM is asking for it**, so the mode defaults to `on` rather than to a second
flag you also have to remember. The three modes:

| `--decision-mode` | What happens |
|---|---|
| `off` | quackd exactly as it has always been. No stepper is built and no backend is imported. `--decision-llm off` says the same thing |
| `shadow` | it is asked on every turn and its answer is recorded beside the model's. **The run is unchanged**: the model still decides everything. This is the mode to start in, and the one that measures |
| `on` | it takes the turns it is confident about, and hands back the rest. Switching it on prints one line saying it has never been measured against a robot, because that is still true |

`QUACKD_DECISION_LLM`, `QUACKD_DECISION_URL` and `QUACKD_DECISION_MODE` do the same when the flag
is absent; the flag wins.

**Asking for a decision LLM that cannot run does not stop the run.** The stepper is an
optimisation and the model is the pilot either way, so a script that always names one still drives
the robot on a machine with no key and no extra. It says so once, before anything is connected,
and carries on without it:

```
$ quackd run arm-grip-check --robot lerobot:mock --llm fake --decision-llm local
⚠ --decision-llm local asked for, running without it: --decision-llm local needs the optional extra quackd[decision] — run: uvx --from "quackd[decision]" quackd ...  or: uv pip install "quackd[decision]"
```

A name nobody defined is a different matter, because that is a typo rather than a missing install,
and it stops the run before anything connects:

```
$ quackd run arm-grip-check --robot lerobot:mock --llm fake --decision-llm nope
✗ error: unknown decision LLM 'nope' from --decision-llm; choose one of jev, kev, von, openjev,
opendecision, local, laya
```

So does a mode nobody defined:

```
$ quackd run arm-grip-check --robot lerobot:mock --llm fake --decision-mode maybe
✗ error: unknown --decision-mode 'maybe'; choose one of off, shadow, on
```

And so does a mode with nobody to answer it, which is the refusal the three-flag split brought
with it. A run that asked for `shadow` and named nothing would quietly do nothing at all, which is
worse than stopping:

```
$ quackd run arm-grip-check --robot lerobot:mock --llm fake --decision-mode shadow
✗ error: --decision-mode shadow needs a decision LLM to run: --decision-llm jev (or
QUACKD_DECISION_LLM)
```

## The SO-101 arm, in four parts

The arm is the only body in this repository that has run on real hardware, so it is where this has
to be explained and where it has to be right.

> **On the SO-101, a decision LLM reads the arm and works the gripper. Every angle is still the
> model's.**

### 1. What the arm can and cannot hand over

| | Tools | Concrete calls | Who answers |
|---|---|---|---|
| **A choice** | `report_state`, `stop`, `place`, `gripper`, `observe`¹ | **6** -- `report_state`, `stop`, `place`, `gripper(open=true)`, `gripper(open=false)`, `observe` | the stepper, or the model |
| **A number** | `move_joints`, `pick` | -- | **always the model** |
| **A sentence** | `assess_task`, `declare_success`, `declare_failure`, `remember` | -- | **always the model** |
| **A segment** | `manipulate`, when a task file lists its instructions² | one per instruction | **always the model**, with the stepper's answer recorded beside it |

¹ `observe` is in the manifest only when a camera is configured, which is why
[`lerobot-lookout`](../ducks/lerobot-lookout.duck) leaves it out.

² With no list its instruction is a free string, a sentence, and it is not offered at all. A v3
task file's `policy.instructions` ([duck-spec.md](duck-spec.md#policy-v3)) make it a closed set,
one call per instruction, and which subtask to hand the policy next is then exactly the choice a
decision LLM might one day make between segments. It is offered and never taken, under
`--decision-mode on` too: it answers to the confirm floor, and under `--yes` nobody is asked at
the confirm gate, so a stepper that cleared the floor would start a learned policy driving the
arm with no person and no model involved. An answer that clears every gate a taken one clears is
recorded with `gate: shadow_only`, the model takes the turn, and the two answers are recorded
side by side (`decision_shadow`), which is the agreement rate a decision to promote it would need.

Nothing here is a special case for the arm, and nothing here is a special case for a vendor. A
verb is a **choice** when every parameter it has is a closed set -- an enum, a constant, or a
boolean -- or is *inert*, meaning it is optional, defaults to null, and accepts null, so leaving
it out chooses nothing. Everything else is a number, and a number is the model's. That rule is
read off each tool's own JSON Schema, so it gives the same answer whichever decision LLM is behind
it, and gives an answer at all for a body quackd has never shipped.

That one rule is why `move_joints` can never be the stepper's, on this arm or the two other bodies
that have one. Its `positions` is a required object. And there is a second reason that is stronger
than the first: **the joint names are not in the schema at all.** They live in a validator, so
there is nothing for a classifier to enumerate even in principle.

### 2. The hero run, call by call

The wave at the top of the README, exactly as it happened on 2026-09-15:

| Call | Tool | Whose turn this would be |
|---|---|---|
| 1 | `report_state` | **the stepper's** |
| 2 | `assess_task` | the model's -- it needs a written reason |
| 3-8 | `move_joints` x6 | the model's -- every one is an angle |
| 9 | `stop` | **the stepper's** |
| 10 | `declare_success` | the model's -- it needs a written reason |

**Two of the ten.** Six of that run's calls author joint angles and can never be the stepper's,
and two more are prose. That is the honest arithmetic on the one run this project has on real
hardware, and it is worth stating plainly rather than choosing a friendlier example.

The stepper is asked on every turn, so this run pays for ten questions to save two model calls.
That still comes out ahead, and by a good margin, but it is the least favourable task in the
repository and that is the reason to lead with it. What it works out to is in [How much faster,
and how much cheaper](#how-much-faster-and-how-much-cheaper).

### 3. Where the arm does hand over: the grip check

[`ducks/arm-grip-check.duck`](../ducks/arm-grip-check.duck) is the same arm and the opposite
shape: read the state, shut the gripper, read it again, release, stop. Every turn is a choice, and
nothing in it authors an angle, because `move_joints` is deliberately not in its allowlist.

```bash
quackd run arm-grip-check --robot lerobot:mock --decision-llm jev        # no arm needed
quackd run arm-grip-check --robot arm-01 --by-hand --decision-llm jev    # something in the gripper
```

With `--by-hand`, the arm is let go at its rest pose, or at the edge of its travel where the
pose lies past it, and quackd takes hold of it again at Enter only when every joint reads inside
its calibrated travel. On an arm whose rest pose was recorded folded past that travel, as
`arm-01`'s `shoulder_lift` was on 2026-09-23, lift the arm clear of the fold before you press
Enter, not only the gripper. Press it over the fold and the take-hold is refused: torque stays
off, nothing moves the arm, and the run ends naming the joint to lift inside its travel
([adapters/lerobot.md](adapters/lerobot.md#placing-it-by-hand)).

Here is a run of it on the mock arm, with a fake standing in for the server, trimmed to the lines
that matter. (Replayed from a recorded transcript and relabelled to what today's code prints. Not
re-run here, because no decision LLM is installed on this machine.)

```text
step 0/12, llm calls 0/12, 0.0/3 min ------------------------------------------
   decide  report_state 0.97 >= 0.60 (0.00 s)
>  verb    report_state() from decision
+  result  report_state ok: shoulder_pan 0, shoulder_lift -90, ...; torque on;
           hottest shoulder_pan 30 degC; holding nothing (0.0 s, 0 intents)
step 1/12, llm calls 0/12, 1 by the stepper, 0.0/3 min ------------------------
   obs     While you were not asked, the stepper chose these (newest last):
           - report_state(): ok - ... holding nothing
   decide  escalate, to the model (0.00 s)
   llm>    step 1: 1 messages (0 with image) to fake scripted:arm-grip-check
   tool    assess_task(verdict='feasible', reason='...')
+  assess  feasible: ...
step 1/12, llm calls 1/12, 1 by the stepper, 0.0/3 min ------------------------
   decide  report_state 0.95 >= 0.60 (0.00 s)
>  verb    report_state() from decision
```

Five things in that are worth reading twice.

- **`from decision`** on the verb line. Who chose a verb is on the record, in the transcript and
  on the terminal, for every call. The line says `from decision` rather than the preset's name
  because what it answers is *who decided*; *which one* is a field on the record beside it, and on
  `run_start` for the whole run.
- **`0.97 >= 0.60`** is the confidence against the floor for that class of verb. A read answers to
  a lower floor than a move; see [How a turn is decided](#how-a-turn-is-decided).
- **`escalate, to the model`** on the second turn. The stepper wanted the gripper, but no
  feasibility verdict had been recorded yet, so the gripper was not among the calls it was
  offered. It handed the turn back and the model recorded the verdict. It was not refused by a
  gate -- it was never offered the option.
- **`While you were not asked, the stepper chose these`**. The model's history contains nothing
  the stepper did, because none of it is anything the model said. It is told instead, once, in the
  observation it is next shown, and told who chose them.
- **`0.00 s`** is a fake answering instantly in a test, not a measurement. A run made today puts
  two more figures in that parenthesis, the tokens the question spent and what they cost; see
  [Measuring it yourself](#measuring-it-yourself). This transcript was recorded before quackd
  counted either, and replays exactly as it was written.

### 4. The other six bodies

The same loop and the same rule, with no per-body code anywhere.
[`microduck-lookout`](../ducks/microduck-lookout.duck) is the most discrete task quackd ships:
`gaze` in five directions, `observe`, `report_state`, `quack` and `stop` are all choices, and only
`say` is not, because it needs a sentence. `open-duck-lookout` is the identical allowlist on an
Open Duck Mini.

A body whose verbs are all numbers -- a cart driving to a pose, an arm moving joints -- escalates
every turn, and the stepper costs it one question a step and nothing else. That is expected, not
broken, and the log says `not_offered` for it without a request being made at all.

## How much faster, and how much cheaper

> [!IMPORTANT]
> **Everything in this section is an estimate, and nobody has run any decision LLM against a
> quackd robot.** It combines two measured things with one published one: quackd's own timings
> from the SO-101 run on 2026-09-15, the size of the request quackd actually builds (measured
> against the mock arm), and TypeSafe's published per-call latency and price. Every input is named
> below so you can disagree with any of them. It rests on the `jev` row because that is the only
> row with published figures at all: the servers you run yourself have no rate card and no latency
> anybody here has measured. `--decision-mode shadow` replaces the whole section with measurements
> from your own bench, and a number from there is worth more than this arithmetic.

### The inputs, and where each one comes from

Two of these are TypeSafe's own figures for Jev, and what they do and do not support is on [its
page](decision-llms/jev.md). The short version is that their headline pair disagrees with their
own worked example, so what is used here is the underlying numbers rather than the multiple.
Nothing in this repository repeats a vendor's multiple as a quackd measurement, and no other row
on that table publishes a latency or a rate at all.

| Figure | Source |
|---|---|
| **0.114 s** per call | [typesafe.ai](https://typesafe.ai/), their worked example ([jev](decision-llms/jev.md)) |
| **$0.042 per million input tokens**, output not charged | [their models page](https://docs.typesafe.ai/models) ([jev](decision-llms/jev.md)) |
| **238x lower input price** than a frontier model | [typesafe.ai](https://typesafe.ai/), against Claude Fable 5.1 |
| **$0** per question on every other row | a server you run bills you in electricity ([pricing](../quackd/agent/providers/pricing.py)) |

And what quackd brings to it, all measured:

| Figure | Source |
|---|---|
| **6.21 s** mean model call | the wave run: 62.1 s over 10 calls ([README](../README.md#what-happened-in-that-run)) |
| **49,096** input tokens over those 10 calls | the same run |
| **527 tokens** per decision request on this arm | measured by hand before the estimator existed: 388 characters of state plus 1,721 of questions, on `lerobot:mock`. The shipped estimator counts the questions at 1,299 characters on that arm before the verdict clears and 1,454 after, because the criteria are one line per verb on offer, so it prints 421 and 460 where this row says 527 |

### One decision

On the turns it can answer, quackd's own baseline gives **6.21 s against 0.114 s, about 54x**.
That is lower than TypeSafe's headline because quackd's baseline is a slower model on a bigger
prompt, and it is the number that matters here.

On price, the whole request is 527 tokens against a model call that averaged 4,910 input tokens on
that run, at a 238th of the price per token. That works out at **about a two-thousandth of the
cost of the call it replaces**, which is a wider gap than TypeSafe's own 445x rather than a
narrower one, and for a reason worth knowing: quackd hands its model a large prompt, nearly five
thousand tokens a call once the contract, the datasheet, the memory and the observations are in
it, while the question it hands the stepper is about a tenth of that. The stepper is cheaper per
token *and* asked a much smaller question.

That ratio assumes the model's input is priced like Claude Fable 5.1, which is the comparison
TypeSafe's own 238x is drawn against. `gpt-6-astra` drove the wave run, and the catalogue now
prices it at $10 per million input tokens, the same input rate as Claude Fable 5.1, so the
comparison holds for the model that actually did the driving. The token counts on both sides are
measured and both rates are the vendors' own, but a rate read off a page by hand on one day is not
an invoice: substitute yours where it differs.

On a server you run yourself the money half of this collapses entirely. There is no rate card,
quackd costs those turns at the self-hosted $0 it already uses for a model on your own machine,
and the only question left is the time one.

### One run: it depends entirely on how many turns are a choice

This is the part a headline multiplier cannot tell you, and it is the honest centre of the
question. The stepper is asked on every turn and only answers some of them, so the saving over a
whole run is capped by that share. With *f* the fraction of turns it answers, *M* the model's mean
call and *L* the stepper's, the think time goes from `N*M` to `N*L + (1-f)*N*M`, so:

**speedup = 1 / (1 - f + L/M)**, and with quackd's numbers *L/M* is 0.018.

| Turns the stepper answers | Think time | Model spend |
|---|---|---|
| 20% | **1.2x** faster | about 20% less |
| 30% | **1.4x** faster | about 30% less |
| 50% | **1.9x** faster | about 50% less |
| 67% | **2.9x** faster | about 67% less |
| 80% | **4.6x** faster | about 80% less |

Two real runs, to put a number on *f* rather than guess at one. Driven on `lerobot:mock` with the
scripted pilot and a fake in the decision LLM's place, `arm-grip-check` answered **4 of its 6
turns** with the stepper and `lerobot-lookout` **3 of 6**, which is 67% and 50%. Those are what
those two runs did rather than a ceiling: a differently answering decision LLM reaches a different
share, and the runs are not a fixed six turns long either. What does hold on a short task is the
floor under the model's share: the feasibility verdict and the closing `declare_success` are
sentences, so they are always the model's, and on a six-turn run that is a third of it before
anything else is counted. The share climbs with the length of the task, which is the opposite of
the usual intuition about where an optimisation pays.

The `L/M` term is what the stepper costs on the turns it *cannot* answer, and at 0.018 it is
almost nothing: even if it answered no turn at all, a run would only be about 2% slower. That is
the asymmetry the whole design rests on. Being wrong about a turn is cheap, and being right is
worth six seconds.

### The wave, end to end

The least favourable task here, worked through:

| | Measured, no stepper | Estimated, `--decision-mode on` |
|---|---|---|
| Model calls | 10 | 8 |
| Stepper calls | 0 | 10 |
| Time spent thinking | 62.1 s | **about 49.2 s** |
| Whole run | 78.8 s | **about 65.9 s**, roughly 16% shorter |
| Model input tokens | 49,096 | roughly a fifth fewer |
| Stepper input tokens | 0 | about 5,270, costing about 0.05% of what the model does |

The two calls that change hands are call 1 (`report_state`, which took 8.2 s) and call 9 (`stop`,
5.8 s). Ten stepper questions at 0.114 s add 1.14 s, so the net is about 12.9 seconds off a 78.8
second run. On `arm-grip-check`, where two turns in three were a choice in the run above, the same
arithmetic gives close to three times less waiting.

### What would make this wrong

- **The real latency against a robot's state.** 0.114 s is TypeSafe's figure on TypeSafe's task,
  and it is the only published one on this page. quackd sends a different shape of request from a
  different network, and a 395M encoder on your own CPU or a 4B decoder on your own GPU is a
  different number again. The per-call timeout is one second, so the worst case is bounded, but
  the worst case is also where the saving goes.
- **How often it is confident enough.** Every turn below its floor escalates and costs the extra
  question with no saving. The tables above assume the turns it answers are the turns it can
  answer, which `--decision-mode shadow` is how you find out.
- **Your verb mix.** `f` is the whole story, and it is a property of the task and the body rather
  than of the decision LLM. An arm doing poses is a different number from an arm checking a grip.
- **Prices move.** Both sides of the ratio are somebody's rate card on a particular day.

## How a turn is decided

One request per turn carrying four questions. They run in parallel and in isolation, so the fourth
is nearly free:

| Question | Type | What it asks |
|---|---|---|
| `next_verb` | Choice | which single call now, out of this turn's options plus `escalate` |
| `done` | Noul | has everything under `success_when` already happened |
| `need_human` | Noul | must a person decide before anything else moves |
| `feasible` | Choice | can this body do this task at all -- **recorded and never acted on**, see below |

They go as plain dicts in the System One shape, `{"type": ..., "instructions": ..., "criteria":
...}`, built once and sent unchanged to a hosted API, to a server on this machine and to a model
in this process. That is the whole reason a new backend is a row rather than a rewrite.

`escalate` is always on the list. Without it a Choice always returns *something*, and the
confidence floor would be the only thing between "none of these is right" and a servo.

The gates are read in this order, and the two Nouls come first so a stepper that thinks the job is
finished never moves anything else:

1. any of the three probabilities is not a number at all -> the model's turn. NaN is not a low
   confidence, it is no confidence, and it loses every comparison it is put through: `nan < 0.85`
   is False, so an unguarded one would clear a floor rather than miss it
2. `done` >= 0.5 -> the model's turn
3. `need_human` >= 0.5 -> the model's turn
4. the answer is `escalate`, or is not one of the calls offered this turn -> the model's turn
5. the answer repeats the call the stepper made last turn -> the model's turn
6. the stepper has answered 8 turns running -> the model's turn
7. confidence is below the floor for that verb's class -> the model's turn
8. otherwise, it is taken

An answer the router cannot read at all -- a confidence that is a word, a `probabilities` that is
a list -- costs that turn and nothing more. Reading the answer is part of the call, so it fails
the way the call does: the error is recorded and the model takes over. That tolerance is
deliberate now that the answer can come from seven different implementations rather than one.

A Noul carries no confidence, so 0.5 on the two of them is a raw probability meaning "more likely
than not". Escalating when the job is not in fact done costs one model call; not escalating when
it is costs a robot that carries on working after the task is over.

**Rules 4 and 5 are why a run always reaches the model.** Only the model can record a verdict,
declare an outcome or write a note, so a run that never reaches it can only end on a budget --
which is exactly what happened the first time this was tried: a stepper answering `report_state`
at 0.99 took all twelve turns of `lerobot-lookout` and the run died with nothing declared. A
reflex that fires twice identically is looping, not deciding.

The floors. TypeSafe's [confidence page](https://docs.typesafe.ai/confidence) publishes exactly
two numbers, and both are here. The other two are quackd's own, set between them, and the table
says which is which because a number nobody published is a number nobody has calibrated either:

| Verb class | Floor | Why that number |
|---|---|---|
| `stop` | **0.50** | **theirs.** Below 0.5 is "genuinely unsure" in TypeSafe's words and the point where their own example routes to a human. quackd puts the brake exactly there instead, because that is where an unsure stepper should still be allowed to reach for it: a wrong `stop` costs one step, a wrong anything-else costs a move nobody chose |
| a read (`report_state`, `observe`, `introspect`) | **0.60** | **quackd's.** Sends no intent at all, so it sits just above their 0.5: a read that is wrong costs a wasted turn and nothing else. TypeSafe publish no number here |
| anything that sends an intent | **0.85** | **quackd's.** Below the 0.9 they pair with "proceed with confirmation", because quackd expresses confirmation separately, and well above their 0.5. Nothing published sits between the two, so this is an appetite for risk rather than a calibration |
| a confirm-gated verb | **0.90** | **theirs,** literally their ">0.9, high stakes, proceed with confirmation" -- and quackd's own confirm gate still asks a person at a terminal on top of it, unless `--yes`, or a pipe or file on stdin, answers for them |
| a `dangerous` verb | never offered | not a floor, a refusal |

> [!WARNING]
> **Two of these are Jev's numbers, two are quackd's, and every decision LLM here inherits all
> four unmeasured.** The floors are one table shared by all seven rows, because a floor is a
> property of quackd's appetite for risk as much as of anybody's calibration -- but the confidence
> they are compared against is computed by whatever answered, and a 4B decoder with a decision
> head, a 395M encoder and a zero-shot classifier do not mean the same thing by 0.87. TypeSafe say
> plainly that the right thresholds depend on your domain and your data. So: `--decision-mode
> shadow` first, on whichever one you picked, and move a floor only once you have a calibration
> curve of your own.

**Why `assess_task` stays the model's.** `feasible|infeasible|uncertain` is a textbook Choice, and
the stepper is asked it on every turn -- but its answer is only recorded, never acted on. The
verdict tool needs a written `reason`, which no decision LLM writes, and it needs `needs`, which
[`own_sheet_objection`](../quackd/verdict.py) reads to refuse a `feasible` verdict that names a
figure the datasheet does not publish. A verdict with an empty `needs` would pass that guard
trivially and silently disable it. And when a pilot answers `uncertain`, quackd puts its reason to
the person in the room; an empty question is not a question. Recording the Choice beside the
model's real verdict is the cheapest way to find out whether a future version should check the
model's verdict rather than write it.

## What it is never allowed to do

Four of these are structural rather than enforced, which is the stronger kind, and all seven
hold whichever decision LLM answered.

- **Author a number.** Not by rule but by construction: a verb with a free number in its schema is
  never a label, so there is no value for the stepper to choose.
- **Author a sentence.** Same reason. Every meta tool takes a required free string.
- **End a run.** `declare_success`, `declare_failure` and `assess_task` are all sentences, so the
  stepper cannot call any of them. Every ending goes through the model or through a budget.
- **Write to memory, or speak to a flock.** `remember` and `tell` are sentences too.
- **See anything.** These are text models -- Jev is documented as text only, and no row here takes
  an image -- so no camera frame ever reaches one. A turn that needs eyes escalates rather than
  guessing.
- **Start a policy segment.** `manipulate` is offered and compared, and never taken, in either
  mode, so every segment of a learned policy driving the arm is one the model asked for.
- **Get past a gate.** The allowlist, the budgets, the confirm gates, the preconditions and the
  robot's own safety authority are exactly what they were. A `.duck` binds the stepper the way it
  binds the model, because it binds the executor and both of them go through it.

And one more that is worth saying out loud: **a decision LLM may never end a run by failing.**
Everything `decide` raises is caught, recorded as a turn with `gate: error`, and handed to the
model. A server that is not listening, a plugin that throws on import, a timeout at one second:
all of them cost that turn and none of them costs the run.

## What the record says

Two kinds in `transcript.jsonl`, both described in
[architecture.md](architecture.md#transcript-format).

`decision`, one per turn the stepper was asked, **identical in both modes** so the rows can be
read against each other: the labels it was offered, the one it chose, the whole probability
distribution, its confidence, the floor that applied, which gate fired, both Noul values, how long
it took, how large the state was and what was trimmed to fit, and, on the turns that actually
reached the network, what the question spent and what that cost.

`decision_shadow`, in shadow mode, and in `on` for a turn that offered a `manipulate` it may not
take, only after that step's `llm` record: what the stepper would have chosen beside what the
model actually chose, whether they agree, whether the stepper
cleared its floor, and what each of them cost. Its fields are `decision_choice`,
`decision_confidence`, `decision_gate`, `decision_latency_s` and `decision_cost_usd` beside
`model_verb`, `llm_latency_s`, `llm_usage` and `llm_cost_usd`. Agreement is about the whole call
rather than the verb's name, because `gripper(open=true)` and `gripper(open=false)` are opposite
instructions that share a word, and on `arm-grip-check` that is most of what there is to compare.
`same_verb` records the coarser reading beside it.

`run_start` gains two fields when a run has one: `decision_price`, the rate that run was costed
at, and `decision_llm`, which is `{name, model, url}` -- which preset answered, which id it
answered as, and where it was reached, with the URL redacted. That last one is new with the split
and it is load-bearing: `jev-1.13.0` names itself, but `kev-latest` on one machine and
`kev-latest` on another are two different servers, and a transcript that recorded only the id
could not tell a reader which of them answered.

`verb_start` says `source: "decision"` for a call the stepper authored, and the tool call ids it
mints are prefixed `decision-`.

`summary.json` grows a `decision` block when there was a stepper, and nothing when there was not:
`mode`, `llm`, `model`, `url`, `asked`, `taken`, `errors`, `latency_s`, `usage`, `cost_usd`,
`cost_estimated` and `price`.

The budget line in every observation grows one clause once the stepper has answered:

```
step 2/12, llm calls 1/12, 2 by the stepper, 0.0/3 min
```

And on the terminal the gutter says `decide` for the stepper's own turn, `decide?` for the
runners-up under it, and `decide=` for a shadow row putting both answers side by side.

## Measuring it yourself

Shadow mode is the benchmark harness. It changes nothing about a run, so it is safe on hardware,
and it records the per-call token figures the hero run never kept.

```bash
# the grip loop on the mock arm: every turn is a choice, and no arm is needed
quackd run arm-grip-check --robot lerobot:mock --llm openai \
  --decision-llm jev --decision-mode shadow --runs-dir runs/bench --no-log

# the same run with no stepper, as the baseline to read it against
quackd run arm-grip-check --robot lerobot:mock --llm openai \
  --runs-dir runs/bench --no-log

# the README's wave, shadowed: the counter-case, on the arm that ran it
quackd run --goal "Wave to the camera with an extended arm" --robot arm-01 \
  --max-steps 10 --decision-llm jev --decision-mode shadow --runs-dir runs/bench

# the same bench against a server you run, which is the comparison nobody has made yet
quackd run arm-grip-check --robot lerobot:mock --llm openai \
  --decision-llm kev --decision-mode shadow --runs-dir runs/bench --no-log
```

`--no-log` on those is about what you watch rather than what is kept. It stops the run narrating
itself on stderr, which is what you want when the point is the rows rather than the watching, and
it shortens `terminal.txt` in the run directory the same way, because that file is the screen.
`transcript.jsonl` and `summary.json` are written in full either way, and every figure below is
read from one of those two.

Then, per run directory:

| What | Read | From |
|---|---|---|
| **Its real latency** against a robot's state, rather than TypeSafe's own 0.114 s on TypeSafe's own task | mean and max of `latency_s` | every `{"kind":"decision"}` |
| **State size** | `state_chars`, `state_tokens_est`, `trimmed` | every `{"kind":"decision"}` |
| **Coverage** | how many `gate` values are `taken`, out of all of them | every `{"kind":"decision"}` |
| **Agreement** | how often `agree` is true, and separately among rows where `would_have_acted` is true | every `{"kind":"decision_shadow"}` |
| **Calibration** | `confidence` bucketed against `agree` -- the plot that earns the right to move a floor, and the one thing that has to be redone per decision LLM | join the two kinds on `step` |
| **What a stepper turn cost** | `usage` and `cost_usd`, with `usage_estimated` for whether that token count came from the server or from quackd's own arithmetic | every `{"kind":"decision"}` |
| **What the model cost** | `latency_s`, `usage`, `cost_usd` | every `{"kind":"llm"}` |
| **The two bills, on one turn** | `llm_cost_usd` beside `decision_cost_usd`: the ratio the section above could only reach by hand, now on the record for the turn that produced it | every `{"kind":"decision_shadow"}` |
| **Which one answered** | `decision_llm` as `{name, model, url}`, and `decision_price` beside it | `run_start` |
| **The rollup** | `steps`, `llm_calls`, `elapsed_s`, `wall_s`, `usage`, `cost_usd`, `decision`, and `command` and `version` beside them, so a bench directory says which flags and which quackd produced the row rather than leaving it to your notes | `summary.json` |
| **The stepper's rollup** | `llm`, `model`, `url`, `usage`, `cost_usd`, `cost_estimated`, and `price`, which is the rate that run was actually costed at rather than whatever the rate is when you read it back | the `decision` block of `summary.json` |

**quackd prices a stepper turn now, and marks the ones it had to guess at.** TypeSafe publish Jev
at **$0.042 per million input tokens, with output not charged**
([models](https://docs.typesafe.ai/models), re-read 2026-09-21), and that is the rate a `jev` turn
is costed at unless `QUACKD_DECISION_PRICE` says otherwise. A server you run yourself is costed at
the self-hosted **$0**, with `self-hosted` recorded as the source, which is a claim about where
the money went rather than a missing figure: it billed you in electricity. The turns that never
reach the network at all, `not_offered` and `state_too_large`, are charged nothing and carry no
figure whatsoever.

Which half of that is measured and which half is arithmetic is never left to be inferred. The rate
is the vendor's, or quackd's own self-hosted zero. The token count under it is measured only where
the backend reports one: where it does not -- and the in-process one never does -- quackd
estimates the request as the state plus the questions at four characters to the token, and flags
the estimate three times over, with `usage_estimated` on the turn, `cost_estimated` on the run's
`decision` block, and a `~` in front of both the tokens and the money on the log line and in front
of the run's cost on the verdict panel.

That path is not a hypothetical. The SDK types both counts on `SystemOneResponse.usage` as `int |
None`, documented as "when the API did not report it"; a model running in this process reports no
count at all; and a call that raised after its request had already left the machine reports
nothing while quite possibly still being billed. quackd charges all three at the estimate rather
than at nothing.

Here is the estimated reading, on the mock arm with a fake in the server's place and no count
coming back from it. The `~` is the whole of the difference, and it sits on both numbers:

```text
   decide  report_state 0.97 >= 0.60 (0.00 s, ~992 tok ~$0.000042)
```

That is 992 tokens rather than the 527 quoted further up, and the arithmetic is on the record:
this turn's state was 2,669 characters and its questions 1,299, which is `(2669 + 1299) // 4`.
Both halves moved. The state grew because a real turn carries a real reading, and the questions
are smaller than the 527 row's 1,721 because that row was measured by hand before this code
existed and because the criteria are one line per verb on offer, which is two here and five once
the verdict clears. The record keeps `state_chars` beside the estimate for exactly that reason: an
estimate you cannot re-derive is a number you have to take on faith.

The model's half of the bill comes from quackd's own price catalogue, or from `--price` where your
rate is negotiated or your model is not in it. The two halves are kept apart **in the record**,
which is where the ratio this section exists to answer is read from: `cost_usd` on every `llm` row
against `cost_usd` on every `decision` row, and both of them on one `decision_shadow` row for the
same turn. The verdict panel is the one place they are added together, because a person watching a
run wants what it cost rather than a division; the exception is a model quackd has no rate for,
where the panel says `cost unpriced (stepper ~$0.0002)` rather than throw away the half it does
know.

None of this has been run against a real server, so the measured path has never returned a real
token count here: every stepper figure in this repository came either from the estimate or from a
fake answering in its place. If you run any of it, the numbers are worth an issue.

## Writing a plugin

A decision LLM that is not a System One server -- its own API, its own transport, a wrapper round
something that was never meant for this -- announces itself through the entry point group
`quackd.decision_llms`, exactly the way a third party's robot adapter does. quackd never has to
know about you, and nothing here has to change to let you in.

One module, one function, and five or six module-level strings that describe the row `doctor` will
print for you:

```python
# mydecider/__init__.py
from collections.abc import Mapping
from typing import Any

SUMMARY = "my own decision LLM, behind our own gateway"
INSTALL = "pip install mydecider"
MODEL = "mydecider-1"  # the id used when the spec names none
URL = "http://127.0.0.1:9000"  # or None, for something with no address
KEY_ENV = "MYDECIDER_API_KEY"  # or None, for something that wants no key
EXTRA = None  # None: quackd found you, so you are installed by definition


class MyDecider:
    def __init__(self, name: str, model: str | None, url: str | None) -> None:
        self.name = name
        self.model = model or MODEL
        self.url = url

    async def decide(
        self, state: Mapping[str, str], questions: Mapping[str, Mapping[str, Any]]
    ) -> Any:
        answers = await my_gateway.ask(dict(state), dict(questions))  # yours
        return {"answers": answers, "usage": {"input_tokens": 512}}


def make(spec: Any, *, url: str | None = None, model: str | None = None) -> MyDecider:
    return MyDecider(spec.name, model, url)
```

```toml
[project.entry-points."quackd.decision_llms"]
mydecider = "mydecider"
```

Then `--decision-llm mydecider` reaches it, `--decision-llm mydecider:some-id` names the model,
and `--decision-url` overrides your `URL`. A built-in name always wins over a plugin that took it,
so a package called `jev` cannot quietly become the thing `--decision-llm jev` reaches.

The protocol is in [`quackd/agent/decision/base.py`](../quackd/agent/decision/base.py) and it is
three attributes and one method:

| | |
|---|---|
| `name` | the preset that built you. What `--decision-llm` took, and what the record says answered |
| `model` | the id you answer as, for the record. Empty where you name none |
| `url` | where you were reached, or `None`. quackd redacts it before writing it down |
| `async decide(state, questions)` | one fan-out. `state` is a flat mapping of short English strings; `questions` is a mapping of name to `{"type": "choice" \| "noul" \| "score", "instructions": ..., "criteria": ...}`. quackd only ever sends the first two |

What you hand back may be anything quackd's reader can get at: an object carrying `answers`, or a
mapping with an `"answers"` key, whose members carry `choice` and `confidence` (and optionally
`probabilities`), or a bare `noul`, as attributes or as keys. A `usage` with `input_tokens` is
used where it is there and estimated where it is not, so a backend that counts nothing costs the
run an estimate rather than an error.

Raising is allowed and is not fatal. `Stepper.advise` catches everything, records the turn as
`gate: error`, and hands it to the model. A plugin may never end a run.

[`tests/stub_decision_llm.py`](../tests/stub_decision_llm.py) is the whole contract as a working
module, and it is short on purpose. It is what the entry-point test loads, and it is the shortest
honest answer to "what do I have to write".

## Configuration

Three flags and four variables. The flag always beats the variable.

| What | Flag | Variable | Default |
|---|---|---|---|
| Which decision LLM answers, as `NAME[:MODEL]` | `--decision-llm` | `QUACKD_DECISION_LLM` | none, and then none of this runs. `off` in either place is off |
| Where your own server listens | `--decision-url` | `QUACKD_DECISION_URL` | whatever the preset's row says; `local` has none and refuses without one |
| What its answer may do, `off\|shadow\|on` | `--decision-mode` | `QUACKD_DECISION_MODE` | `on` once one is named, `off` when none is |
| What a question is costed at, written `in=0.042,out=0` in USD per million tokens | -- | `QUACKD_DECISION_PRICE` | the row's published rate for `jev`, and the self-hosted $0 for every server you run |

`QUACKD_DECISION_PRICE` takes the same form `--price` does for the model, and it is quackd's own
variable rather than any SDK's, which is why it is not spelled `TYPESAFE_*`: everything with that
prefix is read by `typesafe_sdk` itself, while this one quackd reads and applies. Either way the
rate a run was actually costed at is written into `run_start` and into the `decision` block, so
reading a bench directory back next month does not depend on the rate card not having moved.

And the rest:

| What | How |
|---|---|
| Key | `TYPESAFE_API_KEY` for `jev`, in the environment or a `.env`. Every other row needs none, and is sent the word `local` instead |
| Install | `quackd[decision]` for every server row, `quackd[laya]` for the in-process one. Neither is in `quackd[all]` |
| Model | in the spec: `--decision-llm jev:jev-latest`, `--decision-llm laya:multilingual` |

`TYPESAFE_BASE_URL` is not read by quackd at all: it belongs to the SDK, and `--decision-url` is
how you tell quackd where a server is. `TYPESAFE_DEFAULT_MODEL` is honoured for the one row that
declares it, `jev`, as a fallback when the spec names no model -- but naming it in the spec is the
way to say it, because that is the form every other row takes and the only one that reaches the
record as a choice somebody made.

The three flags are on `quackd run` and nowhere else. They are deliberately not on `quackd
record`, which makes the README's recordings and has to stay reproducible without a network call,
and not on `quackd serve-mcp`, where the model is the client and quackd has no think path to sit
in front of.

## Limitations

- **Text only.** No image, audio or video input on any row here
  ([models](https://docs.typesafe.ai/models) for the hosted one). A turn that needs to look
  escalates.
- **A network call in a decision loop**, for six of the seven rows. There is a one-second timeout
  and one retry, and a failure costs that turn and never the run: the error is recorded and the
  model takes over. A CPU-only server on your own machine may well need more than a second, and
  `--decision-mode shadow` is how you find that out before it matters.
- **The in-process one trades the network for a load.** `laya` needs no port and no key, and pays
  about seven seconds the first time a run asks it anything, plus a download the first time ever.
- **English first.** Other languages are supported with lower accuracy, and a `.duck` written in
  one would be worth measuring before trusting.
- **Not the fast loops.** The steering loop runs at 10 Hz and the robot's own controllers faster
  than that. This sits in the slow loop, beside the model, and nothing about it changes what stops
  a body ([safety.md](safety.md)).
- **Confidence is calibrated over groups, not promised per answer.** A 0.93 is not a promise about
  that one answer; it is a statement about how a population of 0.93s behaves -- and it is a
  statement about the population *that backend* produces, which is why the floors are a starting
  point rather than a setting.
- **Only one row has published figures at all.** The speed and cost section is built on Jev's, and
  the six other rows have no rate card and no latency anybody here has measured.
- **Unmeasured here.** No agreement rate, no calibration curve, no hardware run, for any of them.
  Nothing on this page is a measurement of a decision LLM driving a robot, because nobody has done
  that yet.

## Further reading

TypeSafe: [introduction](https://docs.typesafe.ai/introduction) · [System
One](https://docs.typesafe.ai/concepts/system-one) ·
[state](https://docs.typesafe.ai/concepts/state) ·
[Choice](https://docs.typesafe.ai/primitives/choice) ·
[Noul](https://docs.typesafe.ai/primitives/noul) ·
[confidence](https://docs.typesafe.ai/confidence) · [speculative
fan-out](https://docs.typesafe.ai/patterns/fan-out) · [confidence-gated
routing](https://docs.typesafe.ai/patterns/confidence-routing) · [the smart-home
demo](https://docs.typesafe.ai/demos/smart-home)

The open ones, each with a page here: [kev](decision-llms/kev.md) · [von](decision-llms/von.md) ·
[openjev](decision-llms/openjev.md) · [opendecision](decision-llms/opendecision.md) ·
[laya](decision-llms/laya.md), and [TheoLeeCJ/SemIf](https://github.com/TheoLeeCJ/SemIf) (and why
it is not a row, above)

quackd: [architecture](architecture.md) · [safety](safety.md) · [local LLMs](local-llms.md) · [the
LeRobot arm](adapters/lerobot.md) ·
[ADR-0040](adr/0040-a-discrete-stepper-in-front-of-the-model.md)
