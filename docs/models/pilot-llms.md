# Pilot LLMs

The pilot is the model that flies a run. It reads the robot's manifest and datasheet, says
whether this body can do the task at all, plans, calls the robot's verbs one turn at a time and
judges whether the task got done. Every run has one, and it is the only model a run needs. The
other two kinds a run can have, a decision LLM in front of it and a learned policy that drives
the arm for it, are on [the models page](README.md).

## Naming one

`--llm VENDOR[:MODEL]`, one flag carrying both, because a model id means nothing without its
vendor. `--llm anthropic` is that vendor's default model, `--llm openai:gpt-6-astra` names one,
and an id that belongs to one vendor only is enough on its own, so `--llm claude-opus-5` means
Anthropic. The split is at the first colon and no other, so `--llm ollama:llama3:8b` is the
`ollama` preset serving `llama3:8b`.

Which pilot a run gets, first match wins:

1. `--llm` on the command.
2. The `llm` a registered robot keeps (`quackd robot add NAME ... --llm VENDOR[:MODEL]`).
3. `QUACKD_LLM`, in the environment or a `.env`.
4. `fake`, the scripted pilot, which needs no key and no network.

An id a cloud vendor does not list is refused before any key is read, and the refusal prints the
ids that vendor does take. OpenRouter is the one vendor whose list is open: an id quackd does not
carry is checked against OpenRouter's own model list when the run starts
([OpenRouter](#openrouter) below). `quackd list-models` prints every id this build carries, and
`quackd doctor` says which keys and SDKs this machine has.

## The cloud vendors

<!-- generated: pilot-vendors, by scripts/models_docs.py from quackd/agent/providers/factory.py. Change a row there and run the script, not this table. -->

| `--llm` | Who | Extra | Key | Address | Default | Ids |
|---|---|---|---|---|---|---|
| `anthropic` | Anthropic | `quackd[anthropic]` | `ANTHROPIC_API_KEY` | the SDK's own | `claude-opus-5-5` | [12](#anthropic) |
| `openai` | OpenAI | `quackd[openai]` | `OPENAI_API_KEY` | `https://api.openai.com/v1`, or `OPENAI_BASE_URL` | `gpt-6-sol` | [22](#openai) |
| `gemini` | Google | `quackd[gemini]` | `GEMINI_API_KEY`, or `GOOGLE_API_KEY` | the SDK's own | `gemini-3.8-flash` | [9](#gemini) |
| `grok` | xAI | `quackd[grok]` | `XAI_API_KEY` | `https://api.x.ai/v1` | `grok-4.7` | [7](#grok) |
| `mistral` | Mistral AI | `quackd[mistral]` | `MISTRAL_API_KEY` | `https://api.mistral.ai/v1` | `mistral-medium-3-5` | [10](#mistral) |
| `deepseek` | DeepSeek | `quackd[deepseek]` | `DEEPSEEK_API_KEY` | `https://api.deepseek.com` | `deepseek-flash` | [2](#deepseek) |
| `cohere` | Cohere | `quackd[cohere]` | `COHERE_API_KEY`, or `CO_API_KEY` | `https://api.cohere.ai/compatibility/v1` | `command-a-plus-05-2026` | [7](#cohere) |
| `qwen` | Alibaba Cloud (Qwen) | `quackd[qwen]` | `DASHSCOPE_API_KEY` | `https://dashscope-intl.aliyuncs.com/compatible-mode/v1` | `qwen3.8-max` | [21](#qwen) |
| `kimi` | Moonshot AI (Kimi) | `quackd[kimi]` | `MOONSHOT_API_KEY` | `https://api.moonshot.ai/v1` | `kimi-k3` | [4](#kimi) |
| `glm` | Z.ai (GLM) | `quackd[glm]` | `ZAI_API_KEY` | `https://api.z.ai/api/paas/v4` | `glm-5.3` | [19](#glm) |
| `meta` | Meta | `quackd[meta]` | `META_API_KEY`, or `MODEL_API_KEY` | `https://api.meta.ai/v1` | `muse-spark-1.3` | [5](#meta) |
| `openrouter` | OpenRouter, other vendors' models behind one key | `quackd[openrouter]` | `OPENROUTER_API_KEY` | `https://openrouter.ai/api/v1` | `openai/gpt-6-sol` | [6, and ids it lists](#openrouter) |

<!-- end generated: pilot-vendors -->

The extra installs the vendor's SDK, and most of them install the same one, `openai`, because
most vendors speak OpenAI's API. The key goes in the environment or a `.env` file
([`.env.example`](../../.env.example) names every one). Where a vendor's own SDK or examples
export the key under a second name, quackd reads that one when the first is empty, and
`quackd doctor` says which it found. The last column links to every id the vendor has below.

## Local servers

<!-- generated: pilot-local, by scripts/models_docs.py from quackd/agent/providers/local.py. Change a row there and run the script, not this table. -->

| `--llm` | Server | Default address | Extra | Key |
|---|---|---|---|---|
| `local` | any server that speaks OpenAI's API | none: give `--base-url` or `QUACKD_BASE_URL` | `quackd[openai]` | `LOCAL_API_KEY`, or none |
| `ollama` | Ollama | `http://localhost:11434/v1` | `quackd[openai]` | `LOCAL_API_KEY`, or none |
| `vllm` | vLLM | `http://localhost:8000/v1` | `quackd[openai]` | `LOCAL_API_KEY`, or none |
| `llamacpp` | llama.cpp's `llama-server` | `http://localhost:8080/v1` | `quackd[openai]` | `LOCAL_API_KEY`, or none |
| `lmstudio` | LM Studio | `http://localhost:1234/v1` | `quackd[openai]` | `LOCAL_API_KEY`, or none |

<!-- end generated: pilot-local -->

A local preset has no catalogue, because it serves whatever you pulled: the half after the colon
takes any id the server serves, as in `--llm vllm:Qwen/Qwen3-8B`, and with none quackd takes the
first model the server lists. No key is needed, `LOCAL_API_KEY` is sent where a server wants
one, and `OPENAI_API_KEY` is never sent to a local address. A local model is sent the detections
as text unless `--vision` says it takes frames. Setup for each server, the tool-calling flags it
needs and what to expect from a small model are in
[guides/local-llms.md](../guides/local-llms.md).

## The scripted pilot, and `--controller vla`

`--llm fake` is a pilot with no model in it: a rule that plays the starter tasks, reads the
detection summary and never calls anything outside this machine. It is the default when nothing
names a pilot, and what the test suite and the first run in the README use.

`--controller vla` takes the pilot's seat on the SO-101 arm alone. No model is involved: a
scripted pilot hands the arm's learned policy each instruction the task lists, then asks you
whether the arm did it. It needs `--policy-url` and a person at the terminal
([VLAs](vlas.md), and [the arm's page](../adapters/lerobot/README.md#a-scripted-pilot-that-a-person-judges---controller-vla)).

## Over MCP

`quackd serve-mcp` selects no model at all. The MCP client is the pilot: Claude Code, Claude
Desktop or any other client that calls the robot's tools, with whatever model that client runs.
`serve-mcp` takes no `--llm` ([guides/mcp.md](../guides/mcp.md)).

## OpenRouter

`--llm openrouter` reaches other vendors' models through one key, `OPENROUTER_API_KEY`.
quackd carries a few of its ids as rows, below, and `--llm openrouter:AUTHOR/MODEL` names any
other id OpenRouter's public list carries with tool calling on the day the run starts, unless
quackd refuses the id's spelling first. A turn is costed at what OpenRouter says it billed.
**No OpenRouter model has answered a real quackd request.** Its chat requests have reached a
stand-in on 127.0.0.1 through the real `openai` SDK, and nothing else. Everything else about it
is in [guides/openrouter.md](../guides/openrouter.md).

## Gemini Robotics-ER 2

Google's embodied-reasoning model is a pilot row, `gemini:gemini-robotics-er-2-preview`, marked
preview as Google marks it. It plans and reasons about what a camera sees, as any pilot does, and
it calls quackd's verbs like any other row. It is not a VLA: it moves no motor itself, and the
Gemini Robotics model that does is offered to selected partners only
([VLAs](vlas.md#not-served-and-why)). Nothing on record has flown a robot with it.

## Every model id

<!-- generated: pilot-ids, by scripts/models_docs.py from quackd/agent/providers/catalogue.py. Change a row there and run the script, not this table. -->

124 model ids from twelve cloud vendors. Rates are USD per million tokens, input / output / cache read / cache write, as each vendor's own page gave them on 2026-09-23, `openai:gpt-6.1-sol`'s on 2026-10-26, and OpenRouter's list on 2026-10-06. None is a rate the vendor does not publish, and unpriced a model with no public per-token rate at all, which a run records as an unknown cost rather than a free one.

### `anthropic`

| `--llm` | Label | Status | Frames | Notes | USD per Mtok |
|---|---|---|---|---|---|
| `anthropic:claude-opus-5-5` | Claude Opus 5.5 | current | yes | default, asked with `auto` | 4 / 20 / 0.2 / 5 |
| `anthropic:claude-fable-5-1` | Claude Fable 5.1 | current | yes | asked with `auto` | 10 / 50 / 0.25 / 12.5 |
| `anthropic:claude-sonnet-5` | Claude Sonnet 5 | current | yes |  | 2 / 10 / 0.2 / 2.5 |
| `anthropic:claude-haiku-4-5` | Claude Haiku 4.5 | current | yes |  | 1 / 5 / 0.1 / 1.25 |
| `anthropic:claude-fable-5` | Claude Fable 5 | legacy | yes |  | 10 / 50 / 1 / 12.5 |
| `anthropic:claude-opus-5` | Claude Opus 5 | legacy | yes |  | 5 / 25 / 0.5 / 6.25 |
| `anthropic:claude-opus-4-8` | Claude Opus 4.8 | legacy | yes |  | 5 / 25 / 0.5 / 6.25 |
| `anthropic:claude-opus-4-7` | Claude Opus 4.7 | legacy | yes |  | 5 / 25 / 0.5 / 6.25 |
| `anthropic:claude-opus-4-6` | Claude Opus 4.6 | legacy | yes |  | 5 / 25 / 0.5 / 6.25 |
| `anthropic:claude-opus-4-5` | Claude Opus 4.5 | legacy | yes |  | 5 / 25 / 0.5 / 6.25 |
| `anthropic:claude-sonnet-4-6` | Claude Sonnet 4.6 | legacy | yes |  | 3 / 15 / 0.3 / 3.75 |
| `anthropic:claude-sonnet-4-5` | Claude Sonnet 4.5 | legacy | yes |  | 3 / 15 / 0.3 / 3.75 |

### `openai`

| `--llm` | Label | Status | Frames | Notes | USD per Mtok |
|---|---|---|---|---|---|
| `openai:gpt-6-sol` | GPT-6 Sol | current | yes | default, Responses API | 2 / 10 / 0.2 / 2.5 |
| `openai:gpt-6.1-sol` | GPT-6.1 Sol | current | yes | Responses API | 2 / 10 / 0.1 / 2.5 |
| `openai:gpt-6-astra` | GPT-6 Astra | current | yes | Responses API | 10 / 50 / 1 / 12.5 |
| `openai:gpt-6-luna` | GPT-6 Luna | current | yes | Responses API | 0.1 / 0.5 / 0.01 / 0.125 |
| `openai:gpt-5.6-sol` | GPT-5.6 Sol | current | yes |  | 4 / 20 / 0.4 / 5 |
| `openai:gpt-5.6-terra` | GPT-5.6 Terra | current | yes |  | 2 / 12 / 0.2 / 2.5 |
| `openai:gpt-5.6-luna` | GPT-5.6 Luna | current | yes |  | 0.2 / 1.2 / 0.02 / 0.25 |
| `openai:gpt-5.5` | GPT-5.5 | legacy | yes |  | 5 / 30 / 0.5 / none |
| `openai:gpt-5.4` | GPT-5.4 | legacy | yes |  | 2.5 / 15 / 0.25 / none |
| `openai:gpt-5.4-mini` | GPT-5.4 mini | legacy | yes |  | 0.75 / 4.5 / 0.075 / none |
| `openai:gpt-5.4-nano` | GPT-5.4 nano | legacy | yes |  | 0.2 / 1.25 / 0.02 / none |
| `openai:gpt-5.2` | GPT-5.2 | legacy | yes |  | 1.75 / 14 / 0.175 / none |
| `openai:gpt-5.1` | GPT-5.1 | legacy | yes |  | 1.25 / 10 / 0.125 / none |
| `openai:gpt-4.1` | GPT-4.1 | legacy | yes |  | 2 / 8 / 0.5 / none |
| `openai:gpt-4.1-mini` | GPT-4.1 mini | legacy | yes |  | 0.4 / 1.6 / 0.1 / none |
| `openai:gpt-4o` | GPT-4o | legacy | yes |  | 2.5 / 10 / 1.25 / none |
| `openai:gpt-4o-mini` | GPT-4o mini | legacy | yes |  | 0.15 / 0.6 / 0.075 / none |
| `openai:gpt-5.5-pro` | GPT-5.5 Pro | specialised | yes | Responses API | 30 / 180 / none / none |
| `openai:gpt-5.4-pro` | GPT-5.4 Pro | specialised | yes | Responses API | 30 / 180 / none / none |
| `openai:gpt-5.2-pro` | GPT-5.2 Pro | specialised | yes | Responses API | 21 / 168 / none / none |
| `openai:gpt-5.3-codex` | GPT-5.3 Codex | specialised | yes | Responses API | 1.75 / 14 / 0.175 / none |
| `openai:chat-latest` | ChatGPT Instant (moves with ChatGPT) | specialised | yes |  | 5 / 30 / 0.5 / none |

### `gemini`

| `--llm` | Label | Status | Frames | Notes | USD per Mtok |
|---|---|---|---|---|---|
| `gemini:gemini-3.8-flash` | Gemini 3.8 Flash | current | yes | default | 0.75 / 3.75 / 0.075 / 0 |
| `gemini:gemini-3.5-flash-lite` | Gemini 3.5 Flash-Lite | current | yes |  | 0.3 / 2.5 / 0.03 / 0 |
| `gemini:gemini-3.1-pro-preview` | Gemini 3.1 Pro (preview) | preview | yes |  | 2 / 12 / 0.2 / 0 |
| `gemini:gemini-3.1-pro-preview-customtools` | Gemini 3.1 Pro, custom tools (preview) | preview | yes |  | 2 / 12 / 0.2 / 0 |
| `gemini:gemini-3-flash-preview` | Gemini 3 Flash (preview) | preview | yes |  | 0.5 / 3 / 0.05 / 0 |
| `gemini:gemini-robotics-er-2-preview` | Gemini Robotics ER 2 (preview) | preview | yes |  | 1 / 5 / 0.1 / 0 |
| `gemini:gemini-3.7-flash` | Gemini 3.7 Flash | legacy | yes |  | 0.75 / 3.75 / 0.075 / 0 |
| `gemini:gemini-3.6-flash` | Gemini 3.6 Flash | legacy | yes |  | 0.75 / 3.75 / 0.075 / 0 |
| `gemini:gemini-3.5-flash` | Gemini 3.5 Flash | legacy | yes |  | 1.5 / 9 / 0.15 / 0 |

### `grok`

| `--llm` | Label | Status | Frames | Notes | USD per Mtok |
|---|---|---|---|---|---|
| `grok:grok-4.7` | Grok 4.7 | current | yes | default | 2 / 6 / 0.5 / none |
| `grok:grok-4.6` | Grok 4.6 | current | yes |  | 2 / 6 / 0.5 / none |
| `grok:grok-4.5` | Grok 4.5 | current | yes |  | 2 / 6 / 0.3 / none |
| `grok:grok-4.3` | Grok 4.3 | current | yes |  | 1.25 / 2.5 / 0.2 / none |
| `grok:grok-4.20-0309-reasoning` | Grok 4.20 (reasoning) | legacy | yes |  | 1.25 / 2.5 / 0.2 / none |
| `grok:grok-4.20-0309-non-reasoning` | Grok 4.20 (non-reasoning) | legacy | yes |  | 1.25 / 2.5 / 0.2 / none |
| `grok:grok-build-0.1` | Grok Build 0.1 | specialised | yes |  | 1 / 2 / 0.2 / none |

### `mistral`

| `--llm` | Label | Status | Frames | Notes | USD per Mtok |
|---|---|---|---|---|---|
| `mistral:mistral-medium-3-5` | Mistral Medium 3.5 | current | yes | default | 1.5 / 7.5 / 0.15 / none |
| `mistral:mistral-large-2512` | Mistral Large 3 | current | yes |  | 0.5 / 1.5 / 0.05 / none |
| `mistral:mistral-small-2603` | Mistral Small 4 | current | yes |  | 0.15 / 0.6 / 0.015 / none |
| `mistral:ministral-14b-2512` | Ministral 3 14B | open | yes |  | 0.2 / 0.2 / 0.02 / none |
| `mistral:ministral-8b-2512` | Ministral 3 8B | open | yes |  | 0.15 / 0.15 / 0.015 / none |
| `mistral:ministral-3b-2512` | Ministral 3 3B | open | yes |  | 0.1 / 0.1 / 0.01 / none |
| `mistral:codestral-2508` | Codestral 25.08 | specialised | no |  | 0.3 / 0.9 / 0.03 / none |
| `mistral:zai-glm-5-3` | Z.ai GLM 5.3 (hosted by Mistral) | preview | no |  | 1.4 / 4.4 / 0.14 / none |
| `mistral:zai-glm-5-2` | Z.ai GLM 5.2 (hosted by Mistral) | preview | no |  | 1.4 / 4.4 / 0.14 / none |
| `mistral:labs-leanstral-1-5` | Leanstral 1.5 (Lean 4 proofs, Mistral labs) | preview | yes |  | 0 / 0 / 0 / none |

### `deepseek`

| `--llm` | Label | Status | Frames | Notes | USD per Mtok |
|---|---|---|---|---|---|
| `deepseek:deepseek-flash` | DeepSeek V4.1 Flash | current | yes | default | 0.3 / 1.2 / 0.006 / none |
| `deepseek:deepseek-v4-pro` | DeepSeek V4 Pro | legacy | no |  | 1.32 / 3.96 / 0.044 / none |

### `cohere`

| `--llm` | Label | Status | Frames | Notes | USD per Mtok |
|---|---|---|---|---|---|
| `cohere:command-a-plus-05-2026` | Command A+ | current | yes | default | unpriced |
| `cohere:command-a-03-2025` | Command A | current | no |  | unpriced |
| `cohere:command-a-reasoning-08-2025` | Command A Reasoning | specialised | no |  | unpriced |
| `cohere:north-mini-code-1-0` | North Mini Code | specialised | no |  | unpriced |
| `cohere:command-r-plus-08-2024` | Command R+ | legacy | no |  | 2.5 / 10 / none / none |
| `cohere:command-r-08-2024` | Command R | legacy | no |  | 0.15 / 0.6 / none / none |
| `cohere:command-r7b-12-2024` | Command R7B | legacy | no |  | 0.0375 / 0.15 / none / none |

### `qwen`

| `--llm` | Label | Status | Frames | Notes | USD per Mtok |
|---|---|---|---|---|---|
| `qwen:qwen3.8-max` | Qwen3.8 Max | current | yes | default | 2 / 6 / none / none |
| `qwen:qwen3.8-flash` | Qwen3.8 Flash | current | yes |  | 0.15 / 0.47 / none / none |
| `qwen:qwen3.7-plus` | Qwen3.7 Plus | current | yes |  | 0.4 / 1.6 / none / none |
| `qwen:qwen3.7-flash` | Qwen3.7 Flash | current | yes |  | 0.03 / 0.13 / none / none |
| `qwen:qwen3.7-max` | Qwen3.7 Max | legacy | no |  | 2.5 / 7.5 / none / none |
| `qwen:qwen3.6-plus` | Qwen3.6 Plus | legacy | yes |  | 0.5 / 3 / none / none |
| `qwen:qwen3.6-flash` | Qwen3.6 Flash | legacy | yes |  | 0.25 / 1.5 / none / none |
| `qwen:qwen3.5-plus` | Qwen3.5 Plus | legacy | yes |  | 0.4 / 2.4 / none / none |
| `qwen:qwen3.5-flash` | Qwen3.5 Flash | legacy | yes |  | 0.1 / 0.4 / none / none |
| `qwen:qwen-max` | Qwen Max | legacy | no |  | 1.6 / 6.4 / none / none |
| `qwen:qwen-plus` | Qwen Plus | legacy | no |  | 0.4 / 1.2 / none / none |
| `qwen:qwen-flash` | Qwen Flash | legacy | no |  | 0.05 / 0.4 / none / none |
| `qwen:qwen3-coder-flash` | Qwen3 Coder Flash | specialised | no |  | 0.3 / 1.5 / none / none |
| `qwen:qwen3.8-27b` | Qwen3.8 27B | open | yes |  | 0.5 / 3 / none / none |
| `qwen:qwen3.8-2.4t-a95b` | Qwen3.8 2.4T-A95B | open | no |  | 2 / 6 / none / none |
| `qwen:qwen3.6-35b-a3b` | Qwen3.6 35B-A3B | open | yes |  | 0.375 / 2.25 / none / none |
| `qwen:qwen3.6-27b` | Qwen3.6 27B | open | yes |  | 0.6 / 3.6 / none / none |
| `qwen:qwen3.5-397b-a17b` | Qwen3.5 397B-A17B | open | yes |  | 0.6 / 3.6 / none / none |
| `qwen:qwen3.5-122b-a10b` | Qwen3.5 122B-A10B | open | yes |  | 0.4 / 3.2 / none / none |
| `qwen:qwen3.5-27b` | Qwen3.5 27B | open | yes |  | 0.3 / 2.4 / none / none |
| `qwen:qwen3.5-35b-a3b` | Qwen3.5 35B-A3B | open | yes |  | 0.25 / 2 / none / none |

### `kimi`

| `--llm` | Label | Status | Frames | Notes | USD per Mtok |
|---|---|---|---|---|---|
| `kimi:kimi-k3` | Kimi K3 | current | yes | default | 3 / 15 / 0.3 / 3 |
| `kimi:kimi-k2.6` | Kimi K2.6 | current | yes |  | 0.95 / 4 / 0.16 / none |
| `kimi:kimi-k2.7-code` | Kimi K2.7 Code | specialised | yes |  | 0.95 / 4 / 0.19 / none |
| `kimi:kimi-k2.7-code-highspeed` | Kimi K2.7 Code (high speed) | specialised | yes |  | 1.9 / 8 / 0.38 / none |

### `glm`

| `--llm` | Label | Status | Frames | Notes | USD per Mtok |
|---|---|---|---|---|---|
| `glm:glm-5.3` | GLM-5.3 | current | no | default | 1.4 / 4.4 / 0.26 / none |
| `glm:glm-5.3-flash` | GLM-5.3 Flash | current | yes |  | 0.15 / 0.5 / 0.03 / none |
| `glm:glm-5.3-flashx` | GLM-5.3 FlashX | current | yes |  | 0.37 / 1.25 / 0.075 / none |
| `glm:glm-5.2` | GLM-5.2 | legacy | no |  | 1.4 / 4.4 / 0.26 / none |
| `glm:glm-5.1` | GLM-5.1 | legacy | no |  | 1.4 / 4.4 / 0.26 / none |
| `glm:glm-5` | GLM-5 | legacy | no |  | 1 / 3.2 / 0.2 / none |
| `glm:glm-4.7` | GLM-4.7 | legacy | no |  | 0.6 / 2.2 / 0.11 / none |
| `glm:glm-4.7-flash` | GLM-4.7 Flash | legacy | no |  | 0 / 0 / 0 / 0 |
| `glm:glm-4.7-flashx` | GLM-4.7 FlashX | legacy | no |  | 0.07 / 0.4 / 0.01 / none |
| `glm:glm-4.6` | GLM-4.6 | legacy | no |  | 0.6 / 2.2 / 0.11 / none |
| `glm:glm-4.5` | GLM-4.5 | legacy | no |  | 0.6 / 2.2 / 0.11 / none |
| `glm:glm-4.5-x` | GLM-4.5 X | legacy | no |  | 2.2 / 8.9 / 0.45 / none |
| `glm:glm-4.5-air` | GLM-4.5 Air | legacy | no |  | 0.2 / 1.1 / 0.03 / none |
| `glm:glm-4.5-airx` | GLM-4.5 AirX | legacy | no |  | 1.1 / 4.5 / 0.22 / none |
| `glm:glm-4.5-flash` | GLM-4.5 Flash | legacy | no |  | 0 / 0 / 0 / 0 |
| `glm:glm-4-32b-0414-128k` | GLM-4 32B (128k) | legacy | no |  | 0.1 / 0.1 / none / none |
| `glm:glm-4.6v` | GLM-4.6V | specialised | yes |  | 0.3 / 0.9 / 0.05 / none |
| `glm:glm-4.6v-flash` | GLM-4.6V Flash | specialised | yes |  | 0 / 0 / 0 / 0 |
| `glm:glm-4.6v-flashx` | GLM-4.6V FlashX | specialised | yes |  | 0.04 / 0.4 / 0.004 / none |

### `meta`

| `--llm` | Label | Status | Frames | Notes | USD per Mtok |
|---|---|---|---|---|---|
| `meta:muse-spark-1.3` | Muse Spark 1.3 | current | yes | default | 1.25 / 4.25 / 0.15 / none |
| `meta:muse-spark-1.2` | Muse Spark 1.2 | legacy | yes |  | 1.25 / 4.25 / 0.15 / none |
| `meta:muse-spark-1.1` | Muse Spark 1.1 | legacy | yes |  | 1.25 / 4.25 / 0.15 / none |
| `meta:muse-spark-1.3-contributor` | Muse Spark 1.3 (contributor tier, Meta trains on your prompts) | specialised | yes |  | 0.1 / 0.2 / 0.002 / none |
| `meta:muse-spark-1.2-contributor` | Muse Spark 1.2 (contributor tier, Meta trains on your prompts) | specialised | yes |  | 0.1 / 0.2 / 0.002 / none |

### `openrouter`

| `--llm` | Label | Status | Frames | Notes | USD per Mtok |
|---|---|---|---|---|---|
| `openrouter:openai/gpt-6-sol` | GPT-6 Sol (via OpenRouter) | current | yes | default | 2 / 10 / 0.2 / 2.5 |
| `openrouter:openai/gpt-6-luna` | GPT-6 Luna (via OpenRouter) | current | yes |  | 0.1 / 0.5 / 0.01 / 0.125 |
| `openrouter:anthropic/claude-sonnet-5.5` | Claude Sonnet 5.5 (via OpenRouter) | current | yes | asked with `auto` | 2 / 10 / 0.2 / 2.5 |
| `openrouter:anthropic/claude-opus-5.5` | Claude Opus 5.5 (via OpenRouter) | current | yes | asked with `auto` | 4 / 20 / 0.2 / 5 |
| `openrouter:google/gemini-3.8-flash` | Gemini 3.8 Flash (via OpenRouter) | current | yes |  | 0.75 / 3.75 / 0.075 / 0.0416666666666667 |
| `openrouter:x-ai/grok-4.7` | Grok 4.7 (via OpenRouter) | current | yes |  | 2 / 6 / 0.5 / none |

<!-- end generated: pilot-ids -->

## What has run

Two cloud models have flown a real robot, both OpenAI's and both on the SO-101 arm:
`openai:gpt-6-astra` on 2026-09-15, twelve `--goal` runs in one afternoon, and both it and
`openai:gpt-6-sol` on 2026-09-23. `gpt-6-sol` has also been filmed flying the arm's simulator, on
2026-09-28, and on 2026-09-29 it flew a twin of the arm with a learned policy behind it. Four
local runs were made by contributors, Qwen 2.5 Coder 14B on LM Studio and Qwen3-32B-AWQ on vLLM
on an NVIDIA GB10, with their transcripts in [`docs/assets/transcripts/`](../assets/transcripts/).
No other row has a run on a robot on record: each is tested against the test suite's stand-in
for its SDK. The robots those runs were on, and how far each robot has got, are in
[adapters/status.md](../adapters/status.md).
