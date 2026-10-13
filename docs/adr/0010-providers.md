# ADR-0010: One provider protocol, vendor SDKs as optional extras

**Status:** accepted · **Date:** 2026-08-28 · Amended by [ADR-0029](0029-tracing.md) (0.8: the Anthropic request now carries `thinking: {"type": "adaptive", "display": "summarized"}`, because without the display the thinking blocks come back empty and the trace has nothing to show. Read "no `thinking` param" below as what 0.2 sent. `QUACKD_THINKING_DISPLAY=omitted` opts out, and a model that rejects the parameter gets one retry without it) · Amended by [ADR-0031](0031-model-catalogue.md) (0.9: the default model ids in the table below are what the first release shipped, and the verification the three "(verify)" marks promised never happened — all three ids are wrong by 2026-09-12. `--model` for a cloud vendor now takes an id from `catalogue.py` and an unlisted one is refused before a key is read; seven more OpenAI-compatible vendors join the matrix, each one subclass as Grok is; and `serve-mcp` selects no model at all, because the client's own model is the pilot) · Amended by [ADR-0050](0050-a-router-is-a-vendor-whose-list-is-read-on-the-day.md) (on OpenRouter one call per turn is asked for without `parallel_tool_calls`, which almost none of its models list, and the loop keeps the first call when several come back)

**Amended 2026-10-11 and 2026-10-12:** a client built on OpenAI's SDK is told where the
variables that SDK reads for itself may go. It reads `OPENAI_CUSTOM_HEADERS`, `OPENAI_ORG_ID` and
`OPENAI_PROJECT_ID` out of the environment and adds them to every request wherever the client
points, and quackd built its clients with no answer to any of them, so `--llm openai` and every
vendor and local preset that speaks OpenAI's API sent a gateway's lines and OpenAI's account
headers to its own address, and in 3.22.1, the locked release, an `Authorization` line of the
first replaced the vendor's key on every request. The rule, decided with Rok on 2026-10-12, is
that the SDK's own variables follow the SDK's own address variable. At OpenAI's own API, decided
on the whole authority of the address the client is handed and over https, and at the address
`OPENAI_BASE_URL` gives `--llm openai` when no `--base-url` is given, the client is handed the key,
the address, an HTTP client that follows no redirect and a vendor's own `default_headers`, which
only OpenRouter has, and nothing else, so all three go exactly as the SDK sends them, a gateway's
own `Authorization` line included. Everywhere else, at every address a preset or a flag chose, the
client is built through `quackd/agent/providers/openai_headers.py`. The provider's key is named as
`Authorization`, and carried under every spelling the variable gives an authorization line, because
the releases the extras allow merge headers four ways and only that leaves one value, quackd's,
under all four: omitting the variable's line instead takes quackd's own away in 3.22.1, which merges
without regard to case. Every other line of the variable and both account headers are omitted,
except that a line naming one of the headers the SDK writes on every request carries the SDK's own
value, which an omission would otherwise take off with it. A local preset that took its address from
`OPENAI_BASE_URL` is not the SDK's own address: that rung is quackd's ladder for a server you run,
which is sent `--api-key`, `LOCAL_API_KEY` or `not-needed` and never `OPENAI_API_KEY`, as
[ADR-0014](0014-local-llms.md) decided and the code had not done until now. The rule as first
written, on 2026-10-11, named quackd's key at OpenAI's own API as well and omitted the variable's
lines at `OPENAI_BASE_URL`, which took a gateway's credential away from the one address it was
written for, and `request_headers` keeps that stricter reading for a client that should send nothing
ambient anywhere. The address is found before the client is built and handed over, so what the
client is built with is decided on where the requests go, and a blank one is no address. The client
follows no redirect, which is said on the SDK's HTTP client because 1.66.0 has no request option for
it, and a client built through the rule whose headers would still carry an authorization or any
other line quackd answered, or any client whose address reads on the other side of OpenAI's own API
from the one it was handed, is refused before the robot connects, naming the SDK's release. The
Anthropic pilot is built with nothing handed (`anthropic.AsyncAnthropic()`), so the variables its
own SDK reads, `ANTHROPIC_CUSTOM_HEADERS` and `ANTHROPIC_AUTH_TOKEN` among them in 1.9.0, the locked
release, go where that SDK points by itself, Anthropic's API, `ANTHROPIC_BASE_URL` or a profile's
address, which is the same rule.

## Context

"Any LLM" is the promise. Every vendor has its own tool-call wire format and its own idea
of where an image goes in a tool result. quackd's loop must not know any of that.

## Decision

- `LLMProvider.step(system, history, tools) -> ProviderTurn`. History is quackd's own
  `Exchange(observation, decision)` list; tools are Anthropic-shaped
  `{name, description, input_schema}` dicts; each provider renders both.
- Exactly one tool call per turn is *requested* from every vendor and *enforced* by the
  loop (first call wins; zero calls → one re-prompt, then failure).
- `fake` is core and scripted. Real providers live behind extras and lazy imports:

| Provider | Extra | SDK surface | One-call mechanism | Default model (`QUACKD_MODEL` overrides) |
|---|---|---|---|---|
| anthropic | `quackd[anthropic]` | `AsyncAnthropic().messages.create` (`beta.messages.create` with `fallbacks="default"` when available) | `tool_choice={"type":"any","disable_parallel_tool_use":true}` | `claude-opus-5` |
| openai | `quackd[openai]` | `AsyncOpenAI().chat.completions.create` | `tool_choice="required"`, `parallel_tool_calls=False` | `gpt-5` (verify) |
| grok | `quackd[grok]` | OpenAI client, `base_url=https://api.x.ai/v1`, `XAI_API_KEY` | same as openai | `grok-4` (verify) |
| gemini | `quackd[gemini]` | `genai.Client().aio.models.generate_content` | `tool_config.function_calling_config.mode="ANY"` | `gemini-2.5-pro` (verify) |

- Anthropic specifics (per the current API, 2026): no `thinking` param (adaptive is the
  default on Opus 5), no `temperature`, no prefill, `output_config.effort` from
  `QUACKD_EFFORT` (default `medium` — a verb choice is not a proof), `max_tokens` 16000
  (thinking counts against it), images as base64 PNG blocks inside `tool_result`, the
  assistant's raw content blocks (thinking included) replayed verbatim next turn,
  `stop_reason == "refusal"` surfaced as a no-tool turn. Server-side refusal fallbacks are
  requested by default (`QUACKD_ANTHROPIC_FALLBACKS=0` disables) and silently drop to the
  plain endpoint if the installed SDK rejects the parameter.
- OpenAI: a `tool` message cannot carry an image, so the frame follows as a `user` message.
- Gemini: `additionalProperties`/`title`/`default` are stripped from schemas.
- The non-Anthropic default model IDs were not verified against vendor docs at build time
  (no keys, no network in CI); they are env-overridable and flagged in `docs/faq.md`.
- Only the last two observations keep their images in the history sent to the provider.

## Consequences

- Tests stub the SDK clients (`tests/test_providers.py`); CI needs no vendor package.
- Adding a provider = one file + one line in `factory.py` + a row in the README matrix.
