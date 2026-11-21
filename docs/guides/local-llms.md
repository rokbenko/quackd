# Local and open-source LLMs

Yes, local LLMs are supported. Anything that serves the OpenAI Chat Completions API with
tools works: llama.cpp's `llama-server`, vLLM, Ollama, LM Studio, and any other
OpenAI-compatible endpoint. No API key is needed.

```bash
uvx --from "roboglot[openai,microduck]" roboglot run hello-world --llm ollama:qwen3:8b --robot microduck:mock
uvx --from "roboglot[openai,microduck]" roboglot run hello-world --llm vllm:Qwen/Qwen3-8B --robot microduck:mock
uvx --from "roboglot[openai,microduck]" roboglot run hello-world --llm llamacpp --robot microduck:mock
uvx --from "roboglot[openai,microduck]" roboglot run hello-world --llm lmstudio --robot microduck:mock
uvx --from "roboglot[openai,microduck]" roboglot run hello-world --llm local --base-url http://gpu-box:8000/v1 --robot microduck:mock
```

The `openai` extra is the `openai` Python package, which is the client for all of these. The
`microduck` extra is there because a bare `roboglot` carries no robot at all, and
`microduck:mock` is the Microduck's mock: an in-memory test double, which shows the LLM, the
gates and the CLI working and not how a robot behaves.
One flag carries both halves: `--llm ollama:qwen3:8b` is the preset before the first colon and
the LLM after it, and a tag with a colon of its own survives that, because the split is at the
first colon only. Leave the LLM half off, as in `--llm llamacpp`, and Roboglot asks the server
for its LLM list and takes the first one. The LLM half is free text here: any id your server
serves is accepted, because the LLM catalogue `roboglot list-models` prints, and that a cloud
vendor's id is checked against, covers the cloud vendors only. A local preset is never refused
for naming something the catalogue has not heard of.

| Preset (`--llm`) | Default address | Override |
|---|---|---|
| `ollama` | `http://localhost:11434/v1` | `--base-url` or `ROBOGLOT_BASE_URL`, or `--host` to move it to another machine |
| `vllm` | `http://localhost:8000/v1` | same |
| `llamacpp` | `http://localhost:8080/v1` | same |
| `lmstudio` | `http://localhost:1234/v1` | same |
| `local` | none, you must pass one | `--base-url` or `ROBOGLOT_BASE_URL`, never `--host` |

`roboglot doctor` probes all four default addresses and prints which servers are up and what
they serve. An Ollama that answers is also asked where it put each loaded LLM
(`GET /api/ps`): all on the GPU, a share of it, or on the CPU.

**The server can live on another machine, and `--host` moves a preset there.** An NVIDIA Jetson
is the case this project has written up ([guides/jetson.md](jetson.md)): the LLM runs on the
board's GPU and Roboglot stays on your laptop. `--host jetson.local` replaces a preset's `localhost`
with that machine and keeps the preset's own port and path, so `--llm ollama` then asks
`http://jetson.local:11434/v1`. That address answers only once Ollama on the board listens
beyond its own loopback, which it does not by default, and Roboglot's daemon is the same, so the
usual way in is an ssh tunnel and `--host 127.0.0.1`
([guides/jetson.md](jetson.md#from-the-laptop)). The port in `--host` is the one Roboglot's daemon on
the board listens on, 9874 unless you changed it, and never the LLM server's. A cloud vendor's
address is never moved. Where a local server is, the first rung that is set wins, and nothing is
probed:

1. `--base-url`: a URL given for this run is used exactly as given.
2. `--host`, or the host a registered robot was stored with: the preset's address moved to that
   machine, port and path kept.
3. `ROBOGLOT_BASE_URL`, used exactly as given.
4. `OPENAI_BASE_URL`, used exactly as given.
5. `ROBOGLOT_HOST`: the preset's address moved to that machine.
6. The preset's own address, on localhost.

A URL given anywhere is used as given, and a host only ever moves a preset. That is why
`--llm local`, which has no preset address, refuses a host on its own and asks for
`--base-url`. The two hosts sit on different rungs on purpose. One typed for this run or
registered with this robot is a decision about this run, and beats a `.env` line naming an
LLM server's URL. `ROBOGLOT_HOST` is the board you usually use, and does not.

`--host` asks more of the board than its LLM server. It names the machine Roboglot's own daemon
runs on, for its camera, its detector and its health, and a run whose daemon does not answer is
refused before anything connects. For the LLM alone, `--base-url http://127.0.0.1:11434/v1`
through that tunnel asks nothing of the daemon. `roboglot doctor --host` probes the four presets
on that machine instead of this one, which is the quickest way to see where a run will look:

```
LLM servers, the presets on 127.0.0.1 (GET /v1/models, 1.5 s timeout) ─────────────────────────────
┌──────────┬───────────────────────────────────┬─────────────┐
│ preset   │ base url                          │ status      │
├──────────┼───────────────────────────────────┼─────────────┤
│ local    │ set ROBOGLOT_BASE_URL or --base-url │           │
│ ollama   │ http://127.0.0.1:11434/v1         │ not running │
│ vllm     │ http://127.0.0.1:8000/v1          │ not running │
│ llamacpp │ http://127.0.0.1:8080/v1          │ not running │
│ lmstudio │ http://127.0.0.1:1234/v1          │ not running │
└──────────┴───────────────────────────────────┴─────────────┘
```

That came from `roboglot doctor --host 127.0.0.1:19874` on Windows, against Roboglot's daemon
started with `--camera fake --port 19874` and serving a board made of files, not a Jetson.
Each preset kept its own port, and the 19874 went to the daemon alone.

> [!NOTE]
> **A decision LLM is not one of these, and this is the page where that is easiest to get
> wrong.** A decision LLM answers typed questions about a state and generates no text at all,
> so it cannot pilot a robot: `--llm` does not take one, and naming one there, `--llm kev`
> say, is refused as an unknown provider. Two names are in both lists, and the flag says which is
> meant: `--llm local` is an LLM server of your own flying the robot and `--decision-llm local` a
> System One server of your own in front of it, as `--llm openai` is OpenAI's chat LLMs and
> `--decision-llm openai` their Decisions API. Several decision LLMs are servers you run on your own
> machine, exactly like the four presets above, and each one has a page of its own under
> [the hub's table](decision-llms/README.md#the-ones-roboglot-names). They are still not the same kind
> of thing: a System One server speaks `POST /v1/systemone` rather than
> OpenAI's Chat Completions, so `--base-url` is not how you reach one. `--decision-url` is, and
> `--decision-llm` names which one. `--host` does not move one either, so one running on a
> Jetson is reached with `--decision-url` too. It sits in front of whichever provider you did
> pick, for the turns whose answer is a choice rather than a number, and it is off unless you
> name one: [guides/decision-llms/README.md](decision-llms/README.md).

## Server setup

Tool calling has to be switched on in some servers. These are the flags that matter.

**Ollama**

```bash
ollama pull qwen3:8b          # any LLM whose card says it supports tools
ollama serve                  # usually already running as a service
roboglot run hello-world --llm ollama:qwen3:8b --robot microduck:mock
```

On a Jetson, install it with the official script rather than a tarball: the script reads
`/etc/nv_tegra_release` and fetches the JetPack build, and the generic arm64 one carries no
Tegra CUDA, so the board answers off its CPU and nothing tells you. `ollama ps` names the
processor a loaded LLM is on, which is where you find out.

**llama.cpp**

```bash
llama-server -m model.gguf --jinja --port 8080     # --jinja enables the tool-calling chat templates
roboglot run hello-world --llm llamacpp --robot microduck:mock
```

**vLLM**

```bash
vllm serve Qwen/Qwen3-8B --enable-auto-tool-choice --tool-call-parser hermes
roboglot run hello-world --llm vllm:Qwen/Qwen3-8B --robot microduck:mock
```

The `--tool-call-parser` value depends on the LLM family (`hermes` for Qwen and Hermes
LLMs, `llama3_json` for Llama 3.x, `mistral` for Mistral). vLLM's docs list the pairs.

Qwen3 thinks before it answers unless the request says otherwise, and the switch is a chat
template argument rather than a sampling parameter. One reported step of `find-and-kick` spent
150 s and 1717 output tokens on the reasoning before deciding (#12). There are two places to
turn it off. On a server you run yourself, do it once at serve time:

```bash
vllm serve Qwen/Qwen3-8B --enable-auto-tool-choice --tool-call-parser hermes \
  --reasoning-parser qwen3 --default-chat-template-kwargs '{"enable_thinking": false}'
```

On a server somebody else runs, or when you want it per run, send it with the request:

```bash
roboglot run hello-world --llm vllm:Qwen/Qwen3-8B --robot microduck:mock \
  --extra-body '{"chat_template_kwargs": {"enable_thinking": false}}'
```

That flag is a JSON string, and no single spelling of one survives every shell: the line above
is for bash, PowerShell 5.1 wants `'{\"chat_template_kwargs\": {\"enable_thinking\": false}}'`,
and `cmd.exe` wants the whole thing in double quotes with the inner ones escaped. The way round
all of it is a line in `.env`, which every shell leaves alone:

```
ROBOGLOT_EXTRA_BODY='{"chat_template_kwargs": {"enable_thinking": false}}'
```

Single quotes there, or none. Double quotes around JSON make python-dotenv drop the variable
without setting it, and the run then thinks out loud as though you had never written the line.

**LM Studio**

Developer tab → Start Server (default port 1234), load an LLM that supports tools, then
`roboglot run hello-world --llm lmstudio --robot microduck:mock`.

## What to expect from small LLMs

Roboglot asks for exactly one tool call per turn. Frontier LLMs do this reliably. Small
local LLMs sometimes answer with JSON in plain text instead of a native tool call, or
call a verb that is not allowed, or wrap a call in chatter, which makes it no call. Three
things make that workable:

1. **Text fallback.** If a reply has no native tool call, Roboglot reads its text as a call
   only when the answer is nothing but the call, after an optional thinking block at its
   start: one JSON object like `{"name": "walk_to", "arguments": {"target": "ball"}}`, or one
   inside one code fence whatever its opening line names (`json`, `JSON`, `js` or nothing), or
   one inside one `<tool_call>...</tool_call>` block, the wrapper Hermes and Qwen chat templates
   write when the server's tool parser does not take it. The thinking block is
   `<think>...</think>`, or everything up to a first `</think>` where the chat template wrote
   the opening tag itself, and a verb the LLM weighed inside it and dropped is never executed.
   Whitespace, a byte order mark and zero-width characters round the call are ignored.
   Anything else is not a call: a call quoted in a sentence, say to decline it, one of several
   listed as options, a call with a thought after it or a second thought before it, two
   `<tool_call>` blocks, or text after the thinking longer than any call needs (16 KiB). An
   object with no `arguments` is no call for a verb that takes parameters, whose defaults would
   fill what the LLM never wrote, and a verb that takes none is called with none. None of what
   is not a call runs, and the LLM is told so and asked again, in these words:

   ```text
   Your last answer was not a call. Text is a call only when it is exactly one JSON object and nothing else, in this shape: {"name": "<verb>", "arguments": {<parameters>}}. You must call exactly one tool. Choose now.
   ```

   One object in that shape naming a tool the LLM was not given is told so, here for `fly`:

   ```text
   Your last answer called "fly", which is not one of your tools, so nothing was run. You must call exactly one tool. Choose now.
   ```

   A turn that carries the server's own refusal, that a content filter cut short, or that the
   output limit cut off (`finish_reason: "length"`) is not read at all. The transcript marks a
   rescued turn with `stop_reason: "text_fallback"` so you can see how often it happened. The
   system prompt tells local LLMs this shape exists.
2. **One retry.** A turn with no usable call is re-prompted once, then counts as a failure.
   Budgets still apply.
3. **The executor never trusts the LLM.** A disallowed verb or bad parameters come back
   as feedback, not as robot motion.

Vision is off by default for local providers because most local LLMs are text only and
servers reject image parts. The text observation already carries what the camera detected
(`ball at bearing 18° left, ~0.6 m`), which is the designed path. For a vision LLM
(qwen2.5-vl, gemma3, llava and friends) pass `--vision` or set `ROBOGLOT_VISION=1`.

> [!NOTE]
> A robot registered with several `--camera-url` values sends every picture in one message,
> each image preceded by a text part naming its camera (`camera top:`), and the last two
> exchanges keep their images, so two cameras is four pictures in a request rather than two.
> Some servers and some LLMs accept only one image per message and refuse a request that
> carries more. If yours does, register the robot with a single `--camera-url`. Only the
> LeRobot arm reads more than one camera at all, and none of this reaches a local server
> unless `--vision` is on.

**A picture that comes with the task needs the same switch.** `--image sketch.png` hands the
pilot a file rather than the robot a camera, and it is on the first request as an image part
like any frame, so a run is refused before it starts if the pilot takes no images at all. A
local preset starts with vision off whatever the LLM behind it can do, which means `--image`
wants `--vision` or `ROBOGLOT_VISION=1` beside it here exactly as a camera does, and the refusal
says so rather than leaving you to guess. The flag tells Roboglot your server accepts image
parts, and a text only LLM is still a text only LLM with it on.

## Knobs

| Setting | Values | Default for local |
|---|---|---|
| `--llm PRESET:LLM` / `ROBOGLOT_LLM` | any id the server serves after the colon, checked against no catalogue | first entry of `/v1/models` |
| `--base-url` / `ROBOGLOT_BASE_URL` | `http://host:port/v1` | the preset's address |
| `--host` / `ROBOGLOT_HOST` | `HOST` or `HOST:PORT`, the machine Roboglot's daemon runs on ([guides/jetson.md](jetson.md)). Moves a preset's `localhost` there, port kept, at the rung shown above | no host, the preset's own `localhost` |
| `--host-token` / `ROBOGLOT_HOST_TOKEN` | the token that daemon was started with, sent to the daemon and never to the LLM server | no token |
| `--api-key` / `LOCAL_API_KEY` | any string | `not-needed` (servers ignore it), and never `OPENAI_API_KEY` |
| `ROBOGLOT_TOOL_CHOICE` | `auto`, `required`, `none` | `auto` (`none` omits the field for servers that reject it) |
| `--vision` / `ROBOGLOT_VISION` | on, off | off |
| `--extra-body` / `ROBOGLOT_EXTRA_BODY` | one JSON object, merged into the top of every request body | nothing extra is sent |

`parallel_tool_calls` is never sent to local servers, because some reject unknown fields, and
nothing else is added unless `--extra-body` asks for it.

Beside the body, a request carries the headers the `openai` package sends about itself and the
platform it runs on, and no authorization but the key above. The key is `--api-key`, then
`LOCAL_API_KEY`, then the word `not-needed`, and never `OPENAI_API_KEY`, so a hosted key in the
same `.env` stays off a board reached over plain HTTP, where whatever the key is crosses that
network in the clear. A server that wants a key is given it as `LOCAL_API_KEY`. Nothing the
`openai` package reads out of the environment for itself goes with the request either, whichever
rung found the server, `OPENAI_BASE_URL` included: the lines of `OPENAI_CUSTOM_HEADERS`, and
`OPENAI_ORG_ID` and `OPENAI_PROJECT_ID`, go to OpenAI's own API and to the address
`OPENAI_BASE_URL` gives `--llm openai`, and never to a server a local preset reaches
([SECURITY.md](../../SECURITY.md)). So a gateway or an access proxy in front of a server, one
that wants a header of its own such as Cloudflare Access's pair, is not sent one by a local
preset, and Roboglot has no other way to add a header yet. One that takes its token as the
`Authorization` works with that token as `LOCAL_API_KEY`. A server that answers a turn with a
redirect fails that turn, because the client follows none, and the error says where the redirect
pointed and, where it moved the whole API, as a plain-HTTP server that sends everything on to its
HTTPS twin does, the `--base-url` that reaches it.

`--extra-body` works on every provider that speaks OpenAI's API, which is ten of the twelve
cloud vendors and all five local presets, and on Chat Completions and Responses alike, so it
keeps working when a run moves from one to the other. The flag beats the variable, and an empty
object sends nothing, which is how a `.env` line is silenced for a single run. Six keys are
refused because they are Roboglot's to send: `model`, `messages`, `input`, `instructions`,
`tools` and `stream`. The odd one there is `instructions`, which is the system prompt on the
Responses API the way `messages` carries it on Chat Completions. Everything else replaces what
Roboglot would have sent, `tool_choice` included, because overriding it is the point. That cuts
both ways: `n` or `response_format` will reach the server too, and what the LLM answers with
afterwards is yours to live with. In a team the object goes to every member that speaks
OpenAI's API, and there is no per robot value in the registry. On OpenRouter a seventh key,
`models`, is refused as well, and a `provider` object is merged beside the `require_parameters`
Roboglot sends rather than replacing it ([openrouter.md](openrouter.md)).

## Honest notes

- Which local LLM pilots the duck well is an open question we have barely measured. The
  loop was designed so that a weak planner degrades the task, never the robot's balance.
  If you run a local LLM through Roboglot, please share the transcript in a Discussion: it is
  the cheapest way to make this section shorter.

- The next two notes are one contributor's, from runs with no robot in them, on a backend
  Roboglot no longer ships. They are kept for what they say about the pilot's verdicts, and
  neither can be run again as it was.

- **What Qwen3-32B-AWQ did at the feasibility gate, over 108 runs.** From the contributor who
  asked for `--extra-body`, on an NVIDIA GB10, and the largest measurement anybody has made of
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
  where it had moved six in six. In all three the LLM answered `uncertain` rather than
  `infeasible`, which is a question for a person rather than a refusal, and one run named no
  endurance at all and so had nothing to be checked against.

  It fails the other way too, and that half is prompt shaped and still unfixed: asked to carry
  the ball across the room, a body whose sheet says it cannot carry answered `uncertain` four
  times out of six, and in three of those four its own `reason` states the disqualifying fact
  before the verdict contradicts it.

- **`--goal` and the same task as a file are not the same question.** Also theirs, filed as
  [#25](https://github.com/rokbenko/quackd/issues/25). `roboglot run find-and-kick` was
  `feasible` 6 of 6 and succeeded 6 of 6. The same words as
  `--goal "Find the ball and kick it."`, same body, same seed, same LLM, were `uncertain` 5
  of 6 and aborted 5 of 6. They rebuilt the goal contract as a `.glot` whose system prompt
  diffs to zero against a real `--goal` run, reproduced the 5 and 1 split, and changed one
  thing at a time: the persona does nothing, a concrete success criterion does nothing, the
  five step strategy reaches 3 of 6, and narrowing the allowlist from fifteen verbs to six
  reaches 5 of 6 under the duck's verb names and 6 of 6 under the goal's own, which rules out
  the renaming that the first of those two had confounded into it. The pilot's reasons say the rest. On fifteen verbs it answers about
  the world, *"Since the camera currently detects nothing, I cannot determine feasibility
  yet"*. On six it answers about the body, *"No payload, reach, or height requirements exceed
  the robot's limits"*.

  What changed first was the words rather than the allowlist: `assess_task` named "the object
  is out of view" as a reason to be unsure, and the verdict is about the body against its
  datasheet. They measured that on the builds that carried it, and it did not move the
  verdicts: 4 of 30 `feasible` before it and 3 of 30 after, and 5 of 30 on 0.10.0, where they
  coded the reasons blind and 23 of the 25 `uncertain` were still about where the ball was. It
  moved the reasons only a little: of the `uncertain` answers, 42 of 42 had rested on where the
  target was before it and 44 of 51 did after. What moved the verdicts is a line of theirs,
  which Roboglot now puts under an observation until a verdict is recorded, when a detector
  looked at the frame and found nothing: that is not by itself a reason for `uncertain`, which
  is for a limit that turns on the unseen thing's mass or size, or on a figure the datasheet
  does not publish. On 0.10.0 with that line, `feasible` was 27 of 30, and every one of those
  runs found the ball and kicked it. On 02411b3, a build of 2026-09-17, a task the body cannot
  do, carrying the ball, went from 5 of 30 `infeasible` to 17 of 30 with it, and `feasible`
  stayed at none either way. The line they measured had no "by itself", which Roboglot added so
  it says what the rule line says, and nobody has measured it on a build since 0.14.0, which
  changed what the prompt says about a figure nobody published. They also found that a cell of
  six cannot show this: Roboglot sends an LLM no temperature, top_p or seed, so each verdict is
  a draw at the server's own sampling unless `--extra-body` pins it, and the same configuration
  measured at six, thirty and eighty runs told three different stories. These are cells of
  thirty, on one LLM, Qwen3-32B-AWQ with thinking off.
- Each cloud vendor keeps the `tool_choice` its own documentation allows: `required` on OpenAI,
  Grok and DeepSeek, `any` on Mistral, `auto` on Qwen, Kimi, GLM and Meta, none at all on
  Cohere, and per row on OpenRouter. `parallel_tool_calls=False` goes to OpenAI, Grok, Mistral
  and Meta, which document it. The local presets are asked with `auto` by design, for the
  reasons above.
- Ollama, vLLM, llama.cpp and LM Studio evolve quickly. If a flag above is stale, open an
  issue with the server version.
