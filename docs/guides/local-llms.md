# Local and open-source LLMs

Yes, local models are supported. Anything that serves the OpenAI Chat Completions API with
tools works: llama.cpp's `llama-server`, vLLM, Ollama, LM Studio, and any other
OpenAI-compatible endpoint. No API key is needed.

```bash
uvx --from "quackd[openai,microduck]" quackd run find-and-kick --llm ollama:qwen3:8b
uvx --from "quackd[openai,microduck]" quackd run find-and-kick --llm vllm:Qwen/Qwen3-8B
uvx --from "quackd[openai,microduck]" quackd run find-and-kick --llm llamacpp
uvx --from "quackd[openai,microduck]" quackd run find-and-kick --llm lmstudio
uvx --from "quackd[openai,microduck]" quackd run find-and-kick --llm local --base-url http://gpu-box:8000/v1
```

The `openai` extra is the `openai` Python package, which is the client for all of these. The
`microduck` extra is there because a bare `quackd` carries no robot at all: `find-and-kick`
means the cartoon duck, and that is the package the duck lives in.
One flag carries both halves: `--llm ollama:qwen3:8b` is the preset before the first colon and
the model after it, and a tag with a colon of its own survives that, because the split is at the
first colon only. Leave the model half off, as in `--llm llamacpp`, and quackd asks the server
for its model list and takes the first one. The model half is free text here: any id your server
serves is accepted, because the model catalogue `quackd list-models` prints, and that a cloud
vendor's id is checked against, covers the cloud vendors only. A local preset is never refused
for naming something the catalogue has not heard of.

| Preset (`--llm`) | Default address | Override |
|---|---|---|
| `ollama` | `http://localhost:11434/v1` | `--base-url` or `QUACKD_BASE_URL`, or `--host` to move it to another machine |
| `vllm` | `http://localhost:8000/v1` | same |
| `llamacpp` | `http://localhost:8080/v1` | same |
| `lmstudio` | `http://localhost:1234/v1` | same |
| `local` | none, you must pass one | `--base-url` or `QUACKD_BASE_URL`, never `--host` |

`quackd doctor` probes all four default addresses and prints which servers are up and what
they serve. An Ollama that answers is also asked where it put each loaded model
(`GET /api/ps`): all on the GPU, a share of it, or on the CPU.

**The server can live on another machine, and `--host` moves a preset there.** An NVIDIA Jetson
is the case this project has written up ([jetson.md](jetson.md)): the model runs on the board's
GPU and quackd stays on your laptop. `--host jetson.local` replaces a preset's `localhost` with
that machine and keeps the preset's own port and path, so `--llm ollama` then asks
`http://jetson.local:11434/v1`. That address answers only once Ollama on the board listens
beyond its own loopback, which it does not by default, and quackd's daemon is the same, so the
usual way in is an ssh tunnel and `--host 127.0.0.1` ([jetson.md](jetson.md#from-the-laptop)).
The port in `--host` is the one quackd's daemon on the board listens on, 9874 unless you changed
it, and never the model server's. A cloud vendor's address is never moved. Where a local server
is, the first rung that is set wins, and nothing is probed:

1. `--base-url`: a URL given for this run is used exactly as given.
2. `--host`, or the host a registered robot was stored with: the preset's address moved to that
   machine, port and path kept.
3. `QUACKD_BASE_URL`, used exactly as given.
4. `OPENAI_BASE_URL`, used exactly as given.
5. `QUACKD_HOST`: the preset's address moved to that machine.
6. The preset's own address, on localhost.

A URL given anywhere is used as given, and a host only ever moves a preset. That is why
`--llm local`, which has no preset address, refuses a host on its own and asks for
`--base-url`. The two hosts sit on different rungs on purpose. One typed for this run or
registered with this robot is a decision about this run, and beats a `.env` line naming a
model server's URL. `QUACKD_HOST` is the board you usually use, and does not.

`--host` asks more of the board than its model server. It names the machine quackd's own daemon
runs on, for its camera, its detector and its health, and a run whose daemon does not answer is
refused before anything connects. For the model alone, `--base-url http://127.0.0.1:11434/v1`
through that tunnel asks nothing of the daemon. `quackd doctor --host` probes the four presets
on that machine instead of this one, which is the quickest way to see where a run will look:

```
LLM servers, the presets on 127.0.0.1 (GET /v1/models, 1.5 s timeout) ─────────────────────────────
┌──────────┬───────────────────────────────────┬─────────────┐
│ preset   │ base url                          │ status      │
├──────────┼───────────────────────────────────┼─────────────┤
│ local    │ set QUACKD_BASE_URL or --base-url │             │
│ ollama   │ http://127.0.0.1:11434/v1         │ not running │
│ vllm     │ http://127.0.0.1:8000/v1          │ not running │
│ llamacpp │ http://127.0.0.1:8080/v1          │ not running │
│ lmstudio │ http://127.0.0.1:1234/v1          │ not running │
└──────────┴───────────────────────────────────┴─────────────┘
```

That came from `quackd doctor --host 127.0.0.1:19874` on Windows, against quackd's daemon
started with `--camera fake --port 19874` and serving a board made of files, not a Jetson.
Each preset kept its own port, and the 19874 went to the daemon alone.

> [!NOTE]
> **A decision LLM is not one of these, and this is the page where that is easiest to get
> wrong.** A decision LLM answers typed questions about a state and generates no text at all,
> so it cannot pilot a robot: `--llm` does not take one, and naming one there, `--llm kev`
> say, is refused as an unknown provider. Several of them are servers you run on your own
> machine, exactly like the four presets above, and each one has a page of its own under
> [the hub's table](decision-llms.md#the-ones-quackd-names). They are still not the same kind
> of thing: a System One server speaks `POST /v1/systemone` rather than
> OpenAI's Chat Completions, so `--base-url` is not how you reach one. `--decision-url` is, and
> `--decision-llm` names which one. `--host` does not move one either, so one running on a
> Jetson is reached with `--decision-url` too. It sits in front of whichever provider you did
> pick, for the turns whose answer is a choice rather than a number, and it is off unless you
> name one: [decision-llms.md](decision-llms.md).

## Server setup

Tool calling has to be switched on in some servers. These are the flags that matter.

**Ollama**

```bash
ollama pull qwen3:8b          # any model whose card says it supports tools
ollama serve                  # usually already running as a service
quackd run find-and-kick --llm ollama:qwen3:8b
```

On a Jetson, install it with the official script rather than a tarball: the script reads
`/etc/nv_tegra_release` and fetches the JetPack build, and the generic arm64 one carries no
Tegra CUDA, so the board answers off its CPU and nothing tells you. `ollama ps` names the
processor a loaded model is on, which is where you find out.

**llama.cpp**

```bash
llama-server -m model.gguf --jinja --port 8080     # --jinja enables the tool-calling chat templates
quackd run find-and-kick --llm llamacpp
```

**vLLM**

```bash
vllm serve Qwen/Qwen3-8B --enable-auto-tool-choice --tool-call-parser hermes
quackd run find-and-kick --llm vllm:Qwen/Qwen3-8B
```

The `--tool-call-parser` value depends on the model family (`hermes` for Qwen and Hermes
models, `llama3_json` for Llama 3.x, `mistral` for Mistral). vLLM's docs list the pairs.

Qwen3 thinks before it answers unless the request says otherwise, and the switch is a chat
template argument rather than a sampling parameter. One reported step of `find-and-kick` spent
150 s and 1717 output tokens on the reasoning before deciding (#12), and the measured pair under
*Honest notes* below puts the same five decisions at 1,290 output tokens with it on against 263
with it off. There are two places to turn it off. On a server you run yourself, do it once at serve time:

```bash
vllm serve Qwen/Qwen3-8B --enable-auto-tool-choice --tool-call-parser hermes \
  --reasoning-parser qwen3 --default-chat-template-kwargs '{"enable_thinking": false}'
```

On a server somebody else runs, or when you want it per run, send it with the request:

```bash
quackd run find-and-kick --llm vllm:Qwen/Qwen3-8B \
  --extra-body '{"chat_template_kwargs": {"enable_thinking": false}}'
```

That flag is a JSON string, and no single spelling of one survives every shell: the line above
is for bash, PowerShell 5.1 wants `'{\"chat_template_kwargs\": {\"enable_thinking\": false}}'`,
and `cmd.exe` wants the whole thing in double quotes with the inner ones escaped. The way round
all of it is a line in `.env`, which every shell leaves alone:

```
QUACKD_EXTRA_BODY='{"chat_template_kwargs": {"enable_thinking": false}}'
```

Single quotes there, or none. Double quotes around JSON make python-dotenv drop the variable
without setting it, and the run then thinks out loud as though you had never written the line.

**LM Studio**

Developer tab → Start Server (default port 1234), load a model that supports tools, then
`quackd run find-and-kick --llm lmstudio`.

## What to expect from small models

quackd asks for exactly one tool call per turn. Frontier models do this reliably. Small
local models sometimes answer with JSON in plain text instead of a native tool call, or
call a verb that is not allowed, or add chatter. Three things make that workable:

1. **Text fallback.** If a reply has no native tool call, quackd looks for a JSON object
   like `{"name": "walk_to", "arguments": {"target": "ball"}}` in the text and uses it. Only
   the answer is read: an inline `<think>...</think>` block is split off first, so a verb the
   model weighed inside its reasoning and dropped is never executed. The transcript marks a
   rescued turn with `stop_reason: "text_fallback"` so you can see how often it happened. The system prompt tells local models this shape exists.
2. **One retry.** A turn with no usable call is re-prompted once, then counts as a failure.
   Budgets still apply.
3. **The executor never trusts the model.** A disallowed verb or bad parameters come back
   as feedback, not as robot motion.

Vision is off by default for local providers because most local models are text only and
servers reject image parts. The text observation already carries what the camera detected
(`ball at bearing 18° left, ~0.6 m`), which is the designed path. For a vision model
(qwen2.5-vl, gemma3, llava and friends) pass `--vision` or set `QUACKD_VISION=1`.

> [!NOTE]
> A robot registered with several `--camera-url` values sends every picture in one message,
> each image preceded by a text part naming its camera (`camera top:`), and the last two
> exchanges keep their images, so two cameras is four pictures in a request rather than two.
> Some servers and some models accept only one image per message and refuse a request that
> carries more. If yours does, register the robot with a single `--camera-url`. Only the
> LeRobot arm reads more than one camera at all, and none of this reaches a local server
> unless `--vision` is on.

**A picture that comes with the task needs the same switch.** `--image sketch.png` hands the
pilot a file rather than the robot a camera, and it is on the first request as an image part
like any frame, so a run is refused before it starts if the pilot takes no images at all. A
local preset starts with vision off whatever the model behind it can do, which means `--image`
wants `--vision` or `QUACKD_VISION=1` beside it here exactly as a camera does, and the refusal
says so rather than leaving you to guess. The flag tells quackd your server accepts image
parts, and a text only model is still a text only model with it on.

## Knobs

| Setting | Values | Default for local |
|---|---|---|
| `--llm PRESET:MODEL` / `QUACKD_LLM` | any id the server serves after the colon, checked against no catalogue | first entry of `/v1/models` |
| `--base-url` / `QUACKD_BASE_URL` | `http://host:port/v1` | the preset's address |
| `--host` / `QUACKD_HOST` | `HOST` or `HOST:PORT`, the machine quackd's daemon runs on ([jetson.md](jetson.md)). Moves a preset's `localhost` there, port kept, at the rung shown above | no host, the preset's own `localhost` |
| `--host-token` / `QUACKD_HOST_TOKEN` | the token that daemon was started with, sent to the daemon and never to the model server | no token |
| `--api-key` / `LOCAL_API_KEY` | any string | `not-needed` (servers ignore it) |
| `QUACKD_TOOL_CHOICE` | `auto`, `required`, `none` | `auto` (`none` omits the field for servers that reject it) |
| `--vision` / `QUACKD_VISION` | on, off | off |
| `--extra-body` / `QUACKD_EXTRA_BODY` | one JSON object, merged into the top of every request body | nothing extra is sent |

`parallel_tool_calls` is never sent to local servers, because some reject unknown fields, and
nothing else is added unless `--extra-body` asks for it.

`--extra-body` works on every provider that speaks OpenAI's API, which is nine of the eleven
cloud vendors and all five local presets, and on Chat Completions and Responses alike, so it
keeps working when a run moves from one to the other. The flag beats the variable, and an empty
object sends nothing, which is how a `.env` line is silenced for a single run. Six keys are
refused because they are quackd's to send: `model`, `messages`, `input`, `instructions`,
`tools` and `stream`. The odd one there is `instructions`, which is the system prompt on the
Responses API the way `messages` carries it on Chat Completions. Everything else replaces what
quackd would have sent, `tool_choice` included, because overriding it is the point. That cuts
both ways: `n` or `response_format` will reach the server too, and what the model answers with
afterwards is yours to live with. In a flock the object goes to every member that speaks
OpenAI's API, and there is no per robot value in the registry.

Add physics by asking for both extras and naming the backend:

```bash
uvx --from "quackd[openai,mujoco]" quackd run find-and-kick --llm ollama:qwen3:8b --robot microduck:mujoco
```

The duck then walks on upstream's own trained policy instead of sliding around a cartoon. It
also undershoots what it is asked for, which is a harder task for a small model and which the
run states in `report_state` ([ADR-0030](adr/0030-mujoco-physics-backend.md)).

## The same duck in a browser, with no quackd installed

[`web/`](../web/README.md) is a static page that runs the physics simulator through MuJoCo's
WebAssembly build and drives it from any OpenAI compatible server, so a local model can pilot
the duck with no key and no `pip install`. It is live at <https://www.quackd.org/simulator>, and
that copy asks nothing of you first: same page, same Local option, same base-URL box, no checkout.

If your model server is on the same machine, a checkout is still the more reliable route, because
that live copy is served over https and your server is not. Browsers disagree about that pairing:
`http://localhost` counts as a trustworthy origin, so some of them let an https page call a
plaintext server on your own machine and others refuse it as mixed content or want a permission
first. A copy you serve yourself is plain http at both ends, so there is no such argument to have,
and it is also what you run when you are changing the page. `web/serve.py` is the server it
needs, stdlib only, so it is Python but not quackd ([web/README.md](../web/README.md) says
why a plain `http.server` will not do):

```bash
python web/serve.py            # then open http://localhost:8000/simulator/
```

If port 8000 is already a vLLM, which the table above assumes it is, then
`python web/serve.py 8001` moves the page rather than the model server.

Either copy is driven the same way. Pick Local in the page and give it your base URL. Ollama has
to be told to accept the page (`OLLAMA_ORIGINS=* ollama serve`), and llama.cpp, vLLM and LM
Studio need the same CORS permission, whichever copy you opened. The keyboard beside the sentence
box is live at the same time as the model, so you can take the duck off a stalled local model
mid-run with `W` and the transcript records the handover.
What the page has and has not been run against is in [web/README.md](../web/README.md).

## Honest notes

- Which local model pilots the duck well is an open question we have barely measured. The
  loop was designed so that a weak planner degrades the task, never the robot's balance.
  Four transcripts are published here and not one of them is ours: two contributors, two
  servers, two machines nothing in this project has ever run on. They are in
  [`assets/transcripts/`](assets/transcripts/), and they are two pairs that answer different
  questions.

  **Qwen 2.5 Coder 14B on LM Studio**, Apple M2 Pro, 2026-09-03, from the contributor who
  built memory between runs. They are two runs out of more than two:
  [docs/design/memory.md](design/memory.md) records the same model reading the memory block
  and never writing to it *across four runs* before `remember` was moved into the numbered
  strategy. These two were kept, so read them as a selection rather than as the sample:

  | transcript | seed | outcome | steps | LLM calls | tokens in + out | text fallbacks | what it shows |
  |---|---|---|---|---|---|---|---|
  | [`…seed6-memory-read.jsonl`](assets/transcripts/qwen2.5-coder-14b-lmstudio-find-and-kick-seed6-memory-read.jsonl) | 6 | success | 8 | 9 | 29,403 + 244 | 0 | the system prompt carries an earlier run's episode under *What you remember*; the model never calls `remember`; two kicks fall short before the third connects |
  | [`…seed5-remember.jsonl`](assets/transcripts/qwen2.5-coder-14b-lmstudio-find-and-kick-seed5-remember.jsonl) | 5 | success | 4 | 6 | 17,939 + 265 | 0 | the `.duck` body now says `remember` in strategy step 5; after the kick the model returns `remember`, `quack` and `declare_success` in one response, the loop keeps the first (a fact from the verb results) and marks `multiple_tool_calls`, and the other two arrive one per turn after |

  These two are not a chain, and nothing here should be read as one: they ran against
  different memory directories (`memory-qwen3` and `memory-qwen2`), seed 5 started from an
  empty memory block, and the episode seed 6 remembers was written by a run that is not in
  this repository. Seed 5 shows the write and seed 6 shows the read. Neither shows the
  other's half.

  Every turn was a native tool call, none needed the JSON text fallback. The simulator
  clock (`elapsed_s` in `run_end`, which is what the budget counts on `sim2d`) says 14 s and
  11 s; the transcript timestamps say 33 s and 29 s of wall clock, three to nine seconds per
  LLM call. What this pair cannot show: anything about another model, another machine, or a
  harder task than the starter duck.

  **Qwen3-32B-AWQ on vLLM 0.27.2.dev**, an NVIDIA GB10 that is `aarch64`, 2026-09-14, from
  the contributor who asked for `--extra-body` (#12). Not a selection this time but a pair:
  the same build (`739ff84`), the same server, and the same seed on the contributor's word,
  because no transcript records one. The flag is the difference the pair was built around,
  and not the only one: the second run's prompt had grown by two lines in the meantime,
  which is the chain at the end of this section and the reason that run skips `remember`.
  It is still the only measurement anybody has of what the flag actually stops, because
  every test in this repository proves the object reaches the SDK call and none of them
  proves the thinking stops:

  | transcript | seed | outcome | steps | LLM calls | tokens in + out | text fallbacks | what it shows |
  |---|---|---|---|---|---|---|---|
  | [`…seed1-thinking-on.jsonl`](assets/transcripts/qwen3-32b-awq-vllm-find-and-kick-seed1-thinking-on.jsonl) | 1 | success | 4 | 8 | 35,416 + 2,049 | 0 | Qwen3 with its factory default: all eight calls deliberate in the open, 599 to 1,446 characters of it in each row's `thinking` field, 165 to 402 output tokens each. One of the eight bought nothing: it asked for `search_scan` before recording a verdict and the gate refused it. It calls `remember`, and the note it saves is what the other run reads |
  | [`…seed1-thinking-off.jsonl`](assets/transcripts/qwen3-32b-awq-vllm-find-and-kick-seed1-thinking-off.jsonl) | 1 | success | 3 | 5 | 21,802 + 263 | 0 | the same run with `--extra-body '{"chat_template_kwargs": {"enable_thinking": false}}'`: not one `thinking` field in the file, 19 to 130 output tokens a call. Its prompt carries the note and the episode the other run wrote. It skips the `quack` the persona asks for |

  **The drop is real and it is smaller than 2,049 → 263.** The two runs did not take the
  same path, so the totals are not comparable. Five decisions are common to both, and those
  are the honest comparison:

  | decision | thinking on | thinking off |
  |---|---|---|
  | `assess_task` | 373 | 130 |
  | `search_scan` | 297 | 37 |
  | `walk_to` | 263 | 40 |
  | `kick` | 172 | 19 |
  | `declare_success` | 185 | 37 |
  | **the same five** | **1,290** | **263** |

  A factor of 4.9, not 7.8. Everything above that is path, and the path difference is not
  the flag's doing either. The quiet run skipped `remember` because its prompt already held
  the fact it would have saved, and the duck's *Memory* section tells it to: *"Skip it if
  the prompt already remembers the same thing."* It skipped `quack`, which strategy step 5
  and the persona both ask for, and that one is an instruction missed rather than a step
  saved. The thinking run made the opposite mistake: its first call asked for `search_scan`
  before recording a feasibility verdict, the gate refused it, and one of its eight calls
  bought nothing. Neither run followed the contract better than the other.

  **The expensive first call is the verdict, not a warm-up for thinking.** With the flag on
  it is 130 output tokens and 16.0 s, against 19 to 40 tokens and 1.9 to 3.9 s afterwards.
  That first call is `assess_task`, whose `reason` argument is a five sentence paragraph and
  the longest single answer in the quiet run. Every call in that run decodes at 8.1 to 10.2
  tokens a second, the first one included, so its own length accounts for about 13 of those
  16 seconds. What is left over is what a first call costs before a server is warm, and the
  thinking run's first call also runs long past what its own length explains, so the cost
  sits with the first call rather than with the flag.

  **`reasoning_tokens` reads 0 in both, and the thought is still in the file.** vLLM ran
  without `--reasoning-parser`, so the server left the thinking inside `content` and billed
  it as output, which is why output tokens is the column to read. quackd then split the
  `<think>` block out itself before anything else saw the text, so in the transcript it is
  the `llm` row's `thinking` field: 599 to 1,446 characters of it on every one of the eight
  calls with the flag off, and not one such field in the other file. That split is a safety
  property rather than a cosmetic one, because the JSON text fallback reads `turn.text`, and
  a model that weighs a verb inside its reasoning and then rejects it would otherwise have
  that call parsed out of a discarded thought (`quackd/agent/providers/local.py`).

  **This pair is also the first published chain.** The thinking run called `remember` and
  saved *"The ball was found at 43° left, ~0.85 m during initial scan."* That sentence is in
  the other run's `system_prompt` verbatim, under *What you remember*, next to an episode
  quackd wrote from the same run, and the counters move from 1 note and 2 episodes to 2 and
  3, so exactly one of each separates them and no run sat in between. A note written by one
  run, read by the next, both ends in this repository. The Qwen 2.5 pair could not show that
  and neither could anything else here.

  The simulator clock says 6.7 s for both, because the robot did the same thing at the same
  speed, and the transcript timestamps say 195.9 s and 29.6 s of wall clock. On a server you
  run yourself the same result is available once at serve time, with `--reasoning-parser
  qwen3` beside `--default-chat-template-kwargs '{"enable_thinking": false}'`, and that is
  the better answer for a box you own. The flag is what you have on one you do not.

  What this pair cannot show: anything about a harder task than the starter duck, about a
  model this size on a machine that is not a GB10, or about whether the quiet run's missed
  `quack` is a pattern or one run's slip. If you run one, please share the transcript in a
  Discussion or a PR into that folder: it is the cheapest way to make this section shorter.

- **What the same model did at the feasibility gate, over 108 runs.** From the same
  contributor as that pair, on the same GB10, and the largest measurement anybody has made of
  this gate: nine tasks in five classes, three repeats each with thinking on and off, before
  and after the patch in [#24](https://github.com/rokbenko/quackd/pull/24), memory off so that
  each verdict came from the datasheet rather than from yesterday's answer. The transcripts are
  not published, so read the numbers as their report rather than as files you can check: a
  noise floor of about 2 per six run cell, measured from the 48 runs the patch never fired on.

  Before the patch, 54 runs answered 14 `infeasible`, 26 `uncertain` and 14 `feasible`. A
  categorical `cannot` is what the gate does best: climbing a 10 cm step and going up the
  stairs were refused six times out of six each. **The hole was the unpublished figure.** A 45
  minute patrol on a body whose endurance nobody has published came back `feasible` six times
  out of six and the duck walked until its step budget ran out, twice with
  `needs: {"endurance_min": 45}` written in the same record. After the patch that check fired
  three times, on that task and on none of the other eight, and the body moved once in six
  where it had moved six in six. In all three the model answered `uncertain` rather than
  `infeasible`, which is a question for a person rather than a refusal, and one run named no
  endurance at all and so had nothing to be checked against.

  It fails the other way too, and that half is prompt shaped and still unfixed: asked to carry
  the ball across the room, a body whose sheet says it cannot carry answered `uncertain` four
  times out of six, and in three of those four its own `reason` states the disqualifying fact
  before the verdict contradicts it.

- **`--goal` and the same task as a file are not the same question.** Also theirs, filed as
  [#25](https://github.com/rokbenko/quackd/issues/25). `quackd run find-and-kick` was
  `feasible` 6 of 6 and succeeded 6 of 6. The same words as
  `--goal "Find the ball and kick it."`, same body, same seed, same model, were `uncertain` 5
  of 6 and aborted 5 of 6. They rebuilt the goal contract as a `.duck` whose system prompt
  diffs to zero against a real `--goal` run, reproduced the 5 and 1 split, and changed one
  thing at a time: the persona does nothing, a concrete success criterion does nothing, the
  five step strategy reaches 3 of 6, and narrowing the allowlist from fifteen verbs to six
  reaches 5 of 6 under the duck's verb names and 6 of 6 under the goal's own, which rules out
  the renaming that the first of those two had confounded into it. The pilot's reasons say the rest. On fifteen verbs it answers about
  the world, *"Since the camera currently detects nothing, I cannot determine feasibility
  yet"*. On six it answers about the body, *"No payload, reach, or height requirements exceed
  the robot's limits"*.

  What changed here is the words rather than the allowlist: `assess_task` named "the object is
  out of view" as a reason to be unsure, and the verdict is about the body against its
  datasheet. **Whether that moves those numbers is unmeasured.** Nobody here runs that model,
  and the six cells want running again on a build that has this in it.
- The cloud providers keep their stricter settings (`tool_choice="required"`,
  `parallel_tool_calls=False`). Only the local presets use the relaxed ones.
- Ollama, vLLM, llama.cpp and LM Studio evolve quickly. If a flag above is stale, open an
  issue with the server version.
