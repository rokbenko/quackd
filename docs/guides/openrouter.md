# OpenRouter

OpenRouter is a router rather than a lab. One key and one bill reach models made by many vendors,
under ids that name the vendor (`anthropic/claude-opus-5.5`), and OpenRouter decides which
provider serves each call. quackd reaches it as `--llm openrouter`, at `https://openrouter.ai/api/v1`
with `OPENROUTER_API_KEY`, over the same OpenAI-compatible client most of the other vendors use:
`quackd[openrouter]` installs the `openai` package and nothing else. Everything here about
OpenRouter was read off its own documentation on 2026-10-06, and the code is
[`openrouter.py`](../../quackd/agent/providers/openrouter.py).

**No OpenRouter model has answered a real quackd request.**

**Nothing here has ever answered a real robot.**

```bash
uv pip install "quackd[openrouter]"                    # not run here
export OPENROUTER_API_KEY=...                           # not run here: no OpenRouter key on this machine
quackd run find-and-kick --llm openrouter --robot microduck:sim2d    # not run here against OpenRouter
```

## The rows quackd carries

```
$ quackd list-models --llm openrouter
models (--llm VENDOR:MODEL, QUACKD_LLM)
┌────────────┬─────────────────────────────┬────────────────────────────────────┬─────────┬─────────┐
│ provider   │ id                          │ label                              │ status  │ notes   │
├────────────┼─────────────────────────────┼────────────────────────────────────┼─────────┼─────────┤
│ openrouter │ openai/gpt-6-sol            │ GPT-6 Sol (via OpenRouter)         │ current │ default │
│            │ openai/gpt-6-luna           │ GPT-6 Luna (via OpenRouter)        │ current │         │
│            │ anthropic/claude-sonnet-5.5 │ Claude Sonnet 5.5 (via OpenRouter) │ current │         │
│            │ anthropic/claude-opus-5.5   │ Claude Opus 5.5 (via OpenRouter)   │ current │         │
│            │ google/gemini-3.8-flash     │ Gemini 3.8 Flash (via OpenRouter)  │ current │         │
│            │ x-ai/grok-4.7               │ Grok 4.7 (via OpenRouter)          │ current │         │
└────────────┴─────────────────────────────┴────────────────────────────────────┴─────────┴─────────┘
openrouter: the rows above are a selection. Any other id with tool calling on the vendor's own model list works too, as --llm openrouter:AUTHOR/MODEL (or AUTHOR/MODEL:free), checked and priced against that list when a run starts.
```

A row is a model from a vendor quackd already drives on its own API, served by its own maker,
whose entry in OpenRouter's model list on 2026-10-06 named `tools` and `tool_choice` among its
supported parameters, took `image` input and carried no `expiration_date`. The first row is the
default, so `--llm openrouter` runs `openai/gpt-6-sol`. Rates are dollars per million tokens,
OpenRouter's own per-token figures read off its list that day, the base rates rather than the
higher ones some entries charge past a long prompt:

| Id | Asked with | Frames | In / out / cache read / cache write |
|---|---|---|---|
| `openai/gpt-6-sol` | `tool_choice: "required"` | yes | 2 / 10 / 0.2 / 2.5 |
| `openai/gpt-6-luna` | `tool_choice: "required"` | yes | 0.1 / 0.5 / 0.01 / 0.125 |
| `anthropic/claude-sonnet-5.5` | `tool_choice: "auto"` | yes | 2 / 10 / 0.2 / 2.5 |
| `anthropic/claude-opus-5.5` | `tool_choice: "auto"` | yes | 4 / 20 / 0.2 / 5 |
| `google/gemini-3.8-flash` | `tool_choice: "required"` | yes | 0.75 / 3.75 / 0.075 / 0.0416666666666667 |
| `x-ai/grok-4.7` | `tool_choice: "required"` | yes | 2 / 6 / 0.5 / none listed |

The two Claude rows are asked rather than told because they refuse a forced call. OpenRouter's
migration guide for Opus 5.5 says a forced `tool_choice` on it "fails at routing with no
compatible endpoint rather than reaching the provider", and Anthropic lists Sonnet 5.5 beside Opus
5.5 as refusing forced tool use. A turn answered in prose is the loop's to handle: it asks once
more, then ends the run.

## Ids quackd does not carry

Any other id OpenRouter's list carries with tool calling is taken, in two steps.

1. **Its spelling, offline and before a key is read.** An OpenRouter id is `AUTHOR/MODEL`, with
   `:free` the only suffix quackd takes, and a few shapes are refused on their spelling whatever
   the list says, several of them ids OpenRouter does list, for the reasons below.
2. **OpenRouter's public model list, before the first paid call.** quackd reads
   `GET https://openrouter.ai/api/v1/models` once per process, with no key and with its own
   `User-Agent`, and refuses the run if the list does not carry the id (naming the nearest ids it
   does carry), carries it without tool calling, or says it expired on or before today. A list
   that cannot be read within ten seconds, or one that is not in OpenRouter's shape, stops the run
   too, and says so, rather than reading as "no such model". `--base-url` moves the list along
   with the requests.

An id taken that way is asked with `tool_choice: "auto"`, or sent no `tool_choice` at all where
its entry names none, which is the bargain quackd strikes with local models: nobody here has seen
how each provider behind OpenRouter words a refusal of a forced call. It gets the camera frame
when its entry names `image` among its input modalities, and the detections as text when it does
not. It is priced at its entry's base rates, recorded with `source: openrouter` and the day of the
run, for any turn OpenRouter does not bill (below).

```
$ quackd run hello-world --llm openrouter:qwen/qwen3.8-flsh --robot microduck:mock
✗ error: openrouter: 'qwen/qwen3.8-flsh' from --llm is not on OpenRouter's model list. Nearest on
the list: qwen/qwen3.8-flash, qwen/qwen3.7-flash, qwen/qwen3.6-flash. See `quackd list-models --llm
openrouter` for the ids quackd carries.
```

That one ran against OpenRouter's real list, with no key, on 2026-10-06. An id with a suffix needs
`openrouter:` in front of it, because `--llm` splits at the first colon, and quackd says so when
the vendor is left off:

```
$ quackd run hello-world --llm google/gemma-4-31b-it:free --robot microduck:mock
✗ error: unknown provider 'google/gemma-4-31b-it' in 'google/gemma-4-31b-it:free' from --llm. It
reads as an OpenRouter id: --llm openrouter:google/gemma-4-31b-it:free. Pass a vendor, or a vendor
and a model: --llm anthropic, --llm anthropic:claude-opus-5-5. Vendors: fake, anthropic, openai,
gemini, grok, mistral, deepseek, cohere, qwen, kimi, glm, meta, openrouter, local, ollama, vllm,
llamacpp, lmstudio.
```

### Refused before a key is read

Each of these is refused on its spelling alone, with the reason below:

| Shape | Why |
|---|---|
| `~author/...` | a `~` alias always resolves to the newest model of its family, so the model that answers could change under a run that names it |
| `openrouter/...` | OpenRouter's own routers choose the model per request, so neither the model that answers nor its price is known before the call |
| `:batch` | the batch-priced entry is served by OpenRouter's Batch API, and a run asks Chat Completions every turn |
| `:nitro` | a routing variant OpenRouter accepts on any id and does not list, and a request a priority endpoint serves is billed at that endpoint's priority rate; sort providers with --extra-body '{"provider": {"sort": "throughput"}}' instead |
| `:floor` | a routing variant OpenRouter accepts on any id and does not list, and a request a flex endpoint serves is billed at that endpoint's flex rate; sort providers with --extra-body '{"provider": {"sort": "price"}}' instead |
| `:exacto` | a routing variant OpenRouter accepts on any id and does not list, so there is no entry to check it against or price it from |
| `:thinking` | OpenRouter says to use the `reasoning` parameter instead, which --extra-body carries |
| `:extended` | OpenRouter says no model currently offers it |
| `:online` | OpenRouter says to use its `openrouter:web_search` server tool instead |

```
$ quackd run hello-world --llm openrouter:~anthropic/claude-opus-latest --robot microduck:mock
✗ error: openrouter: '~anthropic/claude-opus-latest' from --llm is refused: a `~` alias always
resolves to the newest model of its family, so the model that answers could change under a run that
names it. quackd lists openai/gpt-6-sol, openai/gpt-6-luna, anthropic/claude-sonnet-5.5,
anthropic/claude-opus-5.5, google/gemini-3.8-flash, x-ai/grok-4.7, and any other id with tool
calling on the vendor's own model list works too. See `quackd list-models --llm openrouter`.
```

### Free models

An id ending `:free` is its own entry on OpenRouter's list, with its own providers and its own
limits, so it is taken like any other id quackd does not carry and has no row of its own.
OpenRouter's limits page, read 2026-10-06, allows 20 requests a minute on them, and 50 a day until
10 credits have been bought, 1,000 a day after. A run that goes past either is refused by
OpenRouter with a 429, which ends the run once the SDK's own retries are spent. Which providers
serve a free model, and what they do with a prompt, is up to them and to your OpenRouter privacy
settings (below), which can leave a free model with no provider you allow.

## Where your prompt goes

To OpenRouter, and on to whichever provider it picks for that model, under both their terms: the
system prompt, the conversation and the camera frame wherever the model takes one. OpenRouter's
FAQ, read 2026-10-06: "Prompt and completion are not logged by default. We do zero logging of your
prompts/completions, even if an error occurs, unless you opt-in to logging them." And: "Providers
that do log, or where we have been unable to confirm their policy, will not be routed to unless
the model training toggle is switched on in the privacy settings tab." `--extra-body` narrows the
providers for one run, with `{"provider": {"data_collection": "deny"}}` or
`{"provider": {"zdr": true}}` for zero data retention.

Every request, the model list's included, also carries two headers naming quackd:
`HTTP-Referer: https://github.com/rokbenko/quackd` and `X-OpenRouter-Title: quackd`. OpenRouter
credits an app on its public rankings by them. They say nothing about you, your robot or your
key, and nothing else is added to what a request would carry without them.

## Routing preferences

Every request carries `{"provider": {"require_parameters": true}}`. OpenRouter's provider routing
page: providers "that don't support all the LLM parameters specified in your request can still
receive the request, but will ignore unknown parameters. When you set `require_parameters` to
`true`, the request won't even be routed to that provider." So a `tool_choice` quackd sends is a
`tool_choice` the serving provider honours. `parallel_tool_calls` is never sent: 13 of the 465
models on the list when it was first read on 2026-10-06 named it, and with `require_parameters` a request carrying it
would have reached almost none of them. The loop takes the first call when a model sends several.

Anything else goes through `--extra-body`, and a `provider` object there is merged beside
`require_parameters` rather than replacing it, so `--extra-body '{"provider": {"sort": "price"}}'`
sorts the providers by price and still insists on the parameters.
`{"provider": {"require_parameters": false}}` turns the insisting off. Reasoning settings go the
same way (`--extra-body '{"reasoning": {"effort": "low"}}'`): `QUACKD_OPENAI_REASONING_EFFORT` is
OpenAI's and is not read here, because with `require_parameters` a parameter nobody asked for
can leave a model with no provider at all. One key is refused: `models`, OpenRouter's fallback
list, would let it answer with a model `run_start` does not name, priced as that model.

## What a turn costs

What OpenRouter says it billed. Its usage accounting page, read 2026-10-06: "Full usage details
are now always included automatically in every response", including `usage.cost`, and its FAQ
says "the base currency is US dollars". That figure already knows which provider served the call,
at which tier and with what cache, so quackd records it as the turn's `cost_usd` and marks that
`llm` record `billed: true`, and `summary.json` counts such calls in `billed_calls`.

On a bring-your-own-key call `usage.cost` is only OpenRouter's fee, and the provider bills your
own key for the rest, which OpenRouter reports as `cost_details.upstream_inference_cost`, so the
two are added. A BYOK call that reports no upstream figure is not billed at the fee alone, which
would understate it by the whole of the model's charge: it is costed at the rate instead, as is
any call that comes back with no `cost`. That rate is the row's, or for an id quackd does not
carry, the one the list gave it. `--price` and `QUACKD_PRICE` beat the bill as they beat the
catalogue. A call that ends in an error is costed the same way when OpenRouter reports what it
billed or used. A call that came back with neither a bill nor a rate makes the run's total
unknown from then on, `cost_usd_total: null` on each later `llm` record and a null `cost_usd` in
`summary.json`, rather than a total that leaves it out. The fee OpenRouter charges when you buy
credits, 5.5% with a $0.80 minimum by card and 5% in crypto by its FAQ, is outside every run.

## What to know before you point a robot at it

- **Chat Completions only.** OpenRouter has a Responses API too, but everything above is
  chat-shaped. `QUACKD_OPENAI_API` is not read, and if OpenRouter relays OpenAI's refusal of
  function tools on Chat Completions for a model, the run ends and says to pick another model
  rather than moving.
- **Reasoning goes back as it came.** A model's `reasoning_details`, which carry Claude's signed
  thinking and Gemini's thought signatures, go back unmodified on the assistant turn that made the
  call, which is what OpenRouter asks for. Its Opus 5.5 guide says requests through it "are not
  subject to" Anthropic's thinking-binding enforcement, so the Claude rows have their old camera
  frames trimmed on every call, where `--llm anthropic` trims Opus 5.5 in steps of eight.
- **An error is an error.** OpenRouter can answer `200` with an `error` and no choices, and its
  errors page says to "Check the body for an `error` field even on a `200`". quackd ends the turn
  with OpenRouter's code and message, the provider it came from and the start of what that
  provider said, rather than reading it as a model that declined to call a tool.
- **The model in the record is the one asked for.** Which provider served each turn is not
  recorded.

## Not the decision LLMs OpenRouter also serves

OpenRouter also serves decision LLMs such as Jev, which answer typed questions about a state
rather than pilot anything. Those are the other side of quackd entirely, `--decision-llm`
([decision-llms/README.md](decision-llms/README.md)), and whether any of them can be reached
through OpenRouter from there has not been checked. `--llm openrouter` is OpenRouter's chat
models as the pilot, and nothing on this page touches the stepper.

## VERIFIED (read from OpenRouter's documentation on 2026-10-06, and measured the same day)

| Thing | Value | What quackd does with it |
|---|---|---|
| Model list | `GET /api/v1/models` answered with no key, when it was first read that day: 465 entries, 397 naming `tools`, 390 `tool_choice` and 13 `parallel_tool_calls`, every entry carrying `supported_parameters`, `pricing` and `architecture`. A request sending `anthropic-version` got a list in Anthropic's shape instead, with no `supported_parameters` in it | read once per process, keyless, before the first paid call, with quackd's own `User-Agent`; a list in which no entry names `supported_parameters` is refused as not OpenRouter's. A typo'd id was refused against the real list with its nearest real ids, and a listed one passed the check and stopped at the missing key |
| Attribution | "Site URL for rankings on openrouter.ai" and "Site title for rankings", for `HTTP-Referer` and `X-OpenRouter-Title`; `X-Title` "is still supported for backwards compatibility" | both sent on every request, naming quackd |
| A forced call on Claude | "a forced `tool_choice` on `anthropic/claude-opus-5.5` fails at routing with no compatible endpoint rather than reaching the provider" | the Claude rows are asked with `auto` |
| `require_parameters` | routes only to providers that support every parameter in the request | sent on every request, under `--extra-body` |
| Thinking binding | requests through OpenRouter "are not subject to this enforcement" | frames trimmed on every call |
| `reasoning_details` | "the entire sequence of consecutive reasoning blocks must match", and they "cannot" be rearranged or modified | replayed verbatim on the assistant turn that produced them |
| Usage and cost | usage is "always included automatically in every response"; "the base currency is US dollars" | `usage.cost` is the turn's `cost_usd`, `billed: true` |
| BYOK | the chat API's usage block names `is_byok`, and carries `cost_details.upstream_inference_cost` as the upstream charge, null when the call is not BYOK | added to the fee on a BYOK call, and only there |
| Errors | a `200` can carry an `error` and no `choices` | a ProviderError in OpenRouter's words |
| Variants | catalog variants, `:free` and `:batch` among them, are separate entries on the list; routing variants "are accepted on any model ID at request time" and "are not listed" | only `:free` is taken; the rest are refused on their spelling, listed or not |
| `~` aliases | "always resolve to the newest concrete model", and the list carries them | refused on their spelling |
| Free models | 20 a minute, 50 a day below 10 credits bought and 1,000 a day after | stated here; a 429 ends the run |
| From a browser | the preflight on `/api/v1/chat/completions` answered `204` with `Access-Control-Allow-Origin: *` from both of the demo page's origins, allowing `HTTP-Referer` and `X-OpenRouter-Title` | the browser demo offers the six rows |
| No key | a keyless POST answered `401` | none: a run with no key stops before it posts |

## UNVERIFIED, and what quackd does about each

| Name | The assumption | What quackd does |
|---|---|---|
| `DEFAULT_ON_CHAT` | that `openai/gpt-6-sol` takes function tools through OpenRouter's Chat Completions. quackd's catalogue starts the same model on OpenAI's Responses API under `--llm openai`, the mark it gives models that will not take function tools on Chat Completions | nothing it can test; if OpenRouter relays such a refusal, the run ends on the first call and says to pick another model |
| `TOOLS_HONOURED` | that `require_parameters` leaves a provider that honours `tool_choice` for every row | a model with no such provider is refused by OpenRouter, in its words |
| `AUTO_CALLS_A_TOOL` | that a model asked with `auto` calls a tool | the loop asks once more, then ends the run |
| `REPLAY_ACCEPTED` | that each provider takes its `reasoning_details` back as sent | they are sent verbatim; nothing reads a refusal of them |
| `TRIMMED_HISTORY_ACCEPTED` | that the Claude rows take a history whose old frames were trimmed every call | rests on OpenRouter's statement; the native provider's binding controls are not available here |
| `COST_IS_THE_BILL` | that `usage.cost`, with the upstream figure on BYOK, is what your key is charged | recorded as billed; `--price` overrides it |
| `LIST_PRICE_MATCHES_BILL` | that a row's rate, or the list's, is near what a turn costs | used only for a turn that came back with no bill |
| `NO_MAX_TOKENS` | how OpenRouter treats a request that names no `max_tokens` against a small balance | nothing; a 402 ends the run in OpenRouter's words |
| `FREE_LIMITS` | that the limits above still hold | a 429 ends the run after the SDK's own retries |
| `ROUTED_PROVIDER_VARIES` | which provider served a turn | not recorded |

## Status

It has never answered a real robot, and this page says so until somebody reports that it has. The
model list check ran against OpenRouter itself, keyless. Everything after it ran against a stand-in:
`tests/fake_openrouter.py` answers on 127.0.0.1, and `tests/test_openrouter_wire.py` drives a real
`quackd run` through the real `openai` SDK at it, checking the headers, the body, the replay, the
cost of every turn and every refusal. The browser demo's client sent its request to the same
stand-in under Node. None of that proves OpenRouter answers.

## How to help

Run it on your own key and report what came back: the transcript, the model id, and the charge
OpenRouter's activity page shows for the run beside the `cost_usd` quackd recorded. A bare
`--llm openrouter` first, because the default is the row that rests on the most. Then a Claude
row past eight exchanges with frames, a Gemini row, and an id quackd does not carry, `:free` or
otherwise.
