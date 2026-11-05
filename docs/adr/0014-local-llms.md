# ADR-0014: Local and open-source LLMs are the OpenAI provider with relaxed knobs

**Status:** accepted · **Date:** 2026-08-29

## Context

Asked on Discord: are local LLMs (llama.cpp, vLLM) supported? Every popular local server
(llama.cpp `llama-server`, vLLM, Ollama, LM Studio) exposes OpenAI's Chat Completions API
with tools, but the cloud OpenAI provider hard-required a key, pinned the base URL, and
sent `tool_choice="required"` and `parallel_tool_calls=False`, which some servers reject and
some LLMs ignore. Small LLMs also tend to write tool calls as plain JSON text.

## Decision

- `LocalProvider(OpenAIProvider)` in `quackd/agent/providers/local.py`, selected by preset
  name: `local` (address required), `ollama`, `vllm`, `llamacpp`, `lmstudio` (default
  addresses). `--base-url` / `QUACKD_BASE_URL` override; `--api-key` / `LOCAL_API_KEY`
  optional, `"not-needed"` otherwise (the SDK insists on a string).
- LLM: `--model` / `QUACKD_MODEL`, else the first id from `client.models.list()`.
- Tool calling: `tool_choice="auto"` (env `QUACKD_TOOL_CHOICE`: `auto`, `required`, `none`
  to omit), no `parallel_tool_calls` field, and a **text fallback** that parses one JSON
  object (`name`/`tool`/`function` + `arguments`/`parameters`/`args`/`input`, bare or
  fenced, OpenAI-style nested `function` accepted) whose name is a known tool. Such turns
  are marked `stop_reason="text_fallback"`. Providers may carry a `prompt_hint`, appended to
  the system prompt; the local one states the JSON shape.
- Vision off by default (`supports_vision=False`), `--vision` / `QUACKD_VISION=1` to send
  frames. The text observation already carries the detections.
- Cloud OpenAI and Grok keep the strict settings. Nothing changes for Anthropic or Gemini.
- `quackd doctor` probes the four default addresses (`GET /v1/models`, 1.5 s).

## Consequences

- One class, one wire format, five names. No new dependency: the `openai` extra is the client.
- Weak LLMs degrade the task, never the robot: the executor still validates every call.
- We do not claim any local LLM pilots well; `docs/local-llms.md` says so and asks for
  transcripts.
