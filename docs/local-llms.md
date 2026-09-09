# Local and open-source LLMs

Yes, local models are supported. Anything that serves the OpenAI Chat Completions API with
tools works: llama.cpp's `llama-server`, vLLM, Ollama, LM Studio, and any other
OpenAI-compatible endpoint. No API key is needed.

```bash
uvx --from "quackd[openai]" quackd run find-and-kick --provider ollama --model qwen3:8b
uvx --from "quackd[openai]" quackd run find-and-kick --provider vllm --model Qwen/Qwen3-8B
uvx --from "quackd[openai]" quackd run find-and-kick --provider llamacpp
uvx --from "quackd[openai]" quackd run find-and-kick --provider lmstudio
uvx --from "quackd[openai]" quackd run find-and-kick --provider local --base-url http://gpu-box:8000/v1
```

The `openai` extra is the `openai` Python package, which is the client for all of these.
Leave `--model` off and quackd asks the server for its model list and takes the first one.

| `--provider` | Default address | Override |
|---|---|---|
| `ollama` | `http://localhost:11434/v1` | `--base-url` or `QUACKD_BASE_URL` |
| `vllm` | `http://localhost:8000/v1` | same |
| `llamacpp` | `http://localhost:8080/v1` | same |
| `lmstudio` | `http://localhost:1234/v1` | same |
| `local` | none, you must pass one | same |

`quackd doctor` probes all four default addresses and prints which servers are up and what
they serve.

## Server setup

Tool calling has to be switched on in some servers. These are the flags that matter.

**Ollama**

```bash
ollama pull qwen3:8b          # any model whose card says it supports tools
ollama serve                  # usually already running as a service
quackd run find-and-kick --provider ollama --model qwen3:8b
```

**llama.cpp**

```bash
llama-server -m model.gguf --jinja --port 8080     # --jinja enables the tool-calling chat templates
quackd run find-and-kick --provider llamacpp
```

**vLLM**

```bash
vllm serve Qwen/Qwen3-8B --enable-auto-tool-choice --tool-call-parser hermes
quackd run find-and-kick --provider vllm --model Qwen/Qwen3-8B
```

The `--tool-call-parser` value depends on the model family (`hermes` for Qwen and Hermes
models, `llama3_json` for Llama 3.x, `mistral` for Mistral). vLLM's docs list the pairs.

**LM Studio**

Developer tab → Start Server (default port 1234), load a model that supports tools, then
`quackd run find-and-kick --provider lmstudio`.

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

## Knobs

| Setting | Values | Default for local |
|---|---|---|
| `--model` / `QUACKD_MODEL` | any id the server serves | first entry of `/v1/models` |
| `--base-url` / `QUACKD_BASE_URL` | `http://host:port/v1` | the preset's address |
| `--api-key` / `LOCAL_API_KEY` | any string | `not-needed` (servers ignore it) |
| `QUACKD_TOOL_CHOICE` | `auto`, `required`, `none` | `auto` (`none` omits the field for servers that reject it) |
| `--vision` / `QUACKD_VISION` | on, off | off |

`parallel_tool_calls` is never sent to local servers, because some reject unknown fields.

Add physics by asking for both extras and naming the backend:

```bash
uvx --from "quackd[openai,mujoco]" quackd run find-and-kick --provider ollama --model qwen3:8b --robot microduck:mujoco
```

The duck then walks on upstream's own trained policy instead of sliding around a cartoon. It
also undershoots what it is asked for, which is a harder task for a small model and which the
run states in `report_state` ([ADR-0030](adr/0030-mujoco-physics-backend.md)).

## The same duck in a browser, no Python

[`web/`](../web/README.md) is a static page that runs the physics simulator through MuJoCo's
WebAssembly build and drives it from any OpenAI compatible server, so a local model can pilot
the duck with no key and nothing installed:

```bash
python -m http.server 8000 --directory web    # browsers refuse ES modules over file://
```

Pick Local in the page and give it your base URL. Ollama has to be told to accept the page
(`OLLAMA_ORIGINS=* ollama serve`), and llama.cpp, vLLM and LM Studio need the same CORS
permission. Browsers treat `http://localhost` as trustworthy, so an https page may still call
it. What the page has and has not been run against is in [web/README.md](../web/README.md).

## Honest notes

- Which local model pilots the duck well is an open question we have barely measured. The
  loop was designed so that a weak planner degrades the task, never the robot's balance.
  There are exactly two data points, and they are not ours: the contributor who built
  memory between runs ran `find-and-kick` against **Qwen 2.5 Coder 14B on LM Studio**
  (Apple M2 Pro, 2026-09-03) and the transcripts are in [`assets/transcripts/`](assets/transcripts/):

  | transcript | seed | outcome | steps | LLM calls | tokens in + out | text fallbacks | what it shows |
  |---|---|---|---|---|---|---|---|
  | [`…seed6-memory-read.jsonl`](assets/transcripts/qwen2.5-coder-14b-lmstudio-find-and-kick-seed6-memory-read.jsonl) | 6 | success | 8 | 9 | 29,403 + 244 | 0 | the system prompt carries the previous run's episode under *What you remember*; the model never calls `remember`; two kicks fall short before the third connects |
  | [`…seed5-remember.jsonl`](assets/transcripts/qwen2.5-coder-14b-lmstudio-find-and-kick-seed5-remember.jsonl) | 5 | success | 4 | 6 | 17,939 + 265 | 0 | the `.duck` body now says `remember` in strategy step 5; after the kick the model returns `remember`, `quack` and `declare_success` in one response, the loop keeps the first (a fact from the verb results) and marks `multiple_tool_calls`, and the other two arrive one per turn after |

  Every turn was a native tool call, none needed the JSON text fallback. The simulator
  clock (`elapsed_s` in `run_end`, which is what the budget counts on `sim2d`) says 14 s and
  11 s; the transcript timestamps say 33 s and 29 s of wall clock, three to nine seconds per
  LLM call. What they cannot show: anything about another model, another machine, or a
  harder task than the starter duck. If you run one, please
  share the transcript in a Discussion or a PR into that folder: it is the cheapest way
  to make this section shorter.
- The cloud providers keep their stricter settings (`tool_choice="required"`,
  `parallel_tool_calls=False`). Only the local presets use the relaxed ones.
- Ollama, vLLM, llama.cpp and LM Studio evolve quickly. If a flag above is stale, open an
  issue with the server version.
