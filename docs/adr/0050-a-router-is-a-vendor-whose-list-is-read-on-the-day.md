# ADR-0050: A router is a vendor whose list is read on the day

**Status:** accepted · **Date:** 2026-10-06 · Amends [ADR-0031](0031-model-catalogue.md) (one vendor's tuple is a selection rather than all `--llm` takes after the colon, and its rows carry a price date of their own), [ADR-0041](0041-the-record-says-when-it-ran-and-what-it-cost.md) (a turn's cost can be what the vendor says it billed rather than rate times tokens) and [ADR-0010](0010-providers.md) (one call per turn holds on this vendor without `parallel_tool_calls`) · Implemented in `quackd/agent/providers/openrouter.py`, with changes in the factory, the catalogue, pricing, the loop, the flock planner and the browser demo · Documented in [docs/guides/openrouter.md](../guides/openrouter.md)

## Context

OpenRouter was asked for as a pilot on 2026-10-06. It speaks OpenAI's API, so by the precedent
ADR-0010 set with Grok and ADR-0031 applied seven more times it should be one small subclass: a
base URL, a key and a model list. It is not quite, because OpenRouter is a router rather than a
lab, and it breaks three things ADR-0031 assumed of every vendor.

- **A row is a model its vendor answers for.** OpenRouter's ids name another vendor's model,
  `anthropic/claude-opus-5.5`, and OpenRouter decides per request which provider serves it. It
  answers for the routing; the model's maker and the serving provider answer for the rest.
- **One date covers every rate.** `PRICES_CHECKED` is stamped on every catalogue price in every
  `run_start`, and OpenRouter's rates were read on a day of their own.
- **An id the catalogue does not list is refused before a key is read.** OpenRouter's public model
  list held 465 entries when it was first read on 2026-10-06, 397 of them naming `tools`, and changes by the week.
  Mirroring it would be a tuple stale before the release that shipped it, and refusing everything
  outside a short list would take away the one thing OpenRouter is for.

It also offers two things no other vendor here does. Every response carries what the call was
billed (`usage.cost`), and it fronts the very models whose quirks quackd already encodes per
vendor: OpenRouter's own migration guide says a forced `tool_choice` on Claude Opus 5.5 "fails at
routing with no compatible endpoint rather than reaching the provider".

## Decision

- **`--llm openrouter`, `OPENROUTER_API_KEY`, `quackd[openrouter]`**, which is the `openai`
  package, at `https://openrouter.ai/api/v1`. One subclass of `OpenAIProvider`, as Grok is, and
  five hooks on the base that are inert for every other vendor: default headers, whether a run may
  move to Responses, `--extra-body` keys refused per vendor, fields added to a replayed assistant
  message, and how a chat response is read.
- **Six rows, and any other id OpenRouter's list carries.** `OPEN_ENDED = ("openrouter",)` marks a
  tuple that is a selection. The six are models from vendors quackd already drives natively,
  served by their own makers, whose list entries named `tools` and `tool_choice`, took `image` and
  carried no `expiration_date`; the first, `openai/gpt-6-sol`, is the default, chosen with Rok.
  Their prices carry `checked=OPENROUTER_PRICES_CHECKED`, which `Price.record()` writes in place of
  the global date. An id the tuple does not carry is checked twice: its spelling offline before a
  key is read, refusing `~` aliases, OpenRouter's own routers, `:batch`, the routing variants and
  the deprecated ones even where OpenRouter lists them, with `:free` the only suffix taken; then OpenRouter's public model list,
  read once per process with no key before the first paid call, refusing an id it does not carry,
  carries without `tools`, or marks expired. A list that cannot be read, or is not in OpenRouter's
  shape, refuses the run rather than reading as "no such model". The entry supplies the frame
  decision and a price recorded with `source: openrouter` and the day of the run.
- **One call per turn, asked for where it can be.** Every request carries
  `provider: {"require_parameters": true}` merged under the caller's `--extra-body`, so the call
  reaches only a provider that honours every parameter sent, and never `parallel_tool_calls`,
  which 13 of the 465 named. A row is told (`required`) unless it is a Claude row that refuses a
  forced call: `forced_tools` is read for OpenRouter's rows now as well as Anthropic's. An id the
  tuple does not carry is asked (`auto`), or sent no `tool_choice` where its entry names none,
  ADR-0014's bargain rather than a 400 reader written against a refusal nobody here has seen.
  The run stays on Chat Completions whatever `QUACKD_OPENAI_API` says, does not read
  `QUACKD_OPENAI_REASONING_EFFORT`, and refuses `models` in `--extra-body`, OpenRouter's fallback
  list, for the reason `model` is refused everywhere.
- **The answer as OpenRouter shapes it.** A model's `reasoning_details` go back verbatim on the
  assistant turn that produced them, carrying Claude's signed thinking and Gemini's signatures.
  OpenRouter says requests through it "are not subject to" Anthropic's thinking-binding
  enforcement, so the native `frame_trim_period` machinery is not used here. A `200` carrying only
  an `error`, or a choice that finished with one, is a ProviderError in OpenRouter's words; a
  refusal is a turn with no call that says so.
- **A turn costs what it was billed.** `ProviderTurn.billed_usd` is `usage.cost`, plus
  `cost_details.upstream_inference_cost` on a bring-your-own-key call and only there, and None
  where the call says nothing usable. `pricing.turn_cost` costs a call at a rate a person named
  (`--price`, `QUACKD_PRICE`), else the bill, else the rate times the tokens, and the loop and the
  flock planner both use it. A billed call's `llm` record says `billed: true` and the summary
  counts them in `billed_calls`, both written only when there are any. A run's total becomes null
  from the first call that came back with neither a bill nor a rate.
- **Attribution.** Every request, the list's included, carries
  `HTTP-Referer: https://github.com/rokbenko/quackd` and `X-OpenRouter-Title: quackd`, decided
  with Rok: they credit quackd on OpenRouter's public app rankings and carry nothing about the
  person running it.
- **The browser offers the six rows**, its preflight having answered `204` with
  `Access-Control-Allow-Origin: *` from both of the page's origins on 2026-10-06, and asks them as
  the CLI does.

## Why not

- **`--llm local --base-url https://openrouter.ai/api/v1`.** It runs today, and that is the
  problem. A local preset is priced at the self-hosted `$0`, so a frontier model through it is
  recorded as free, the most expensive kind of wrong ADR-0041 names. It also has no default, no
  check of the id and no attribution, and asks every model with `auto`.
- **Mirroring OpenRouter's whole list.** Hundreds of rows, read by hand, stale within the week,
  in a module every press of TAB imports.
- **Refusing every id outside the six.** ADR-0031's rule applied literally, and decided against
  with Rok: it keeps the catalogue's guarantee by throwing away the reason anybody uses OpenRouter.
- **Taking an unlisted id unchecked.** A typo would then cost a connected robot and a failed call,
  which is the failure ADR-0031 was written to end. The public list makes the same check without
  a key.
- **A 400 reader for a forced call an unlisted model refuses.** Nobody here has seen how each
  provider behind OpenRouter words one, and `anthropic.py`'s thinking retry once missed the real
  sentence for being written against a guessed one.
- **Costing at the list rate only.** It understates a call OpenRouter routed to a dearer
  endpoint, a priority tier or a long-prompt rate, and the bill is in every response. Decided
  with Rok.
- **`:free` rows.** Which providers serve a free model, and what they keep, varies by provider and
  by the person's own OpenRouter settings. Every `:free` id is reachable as an unlisted one.

## Consequences

- Twelve cloud vendors, 123 catalogue ids, and still three SDKs.
- An id the tuple does not carry costs one public GET per process, with a ten-second ceiling,
  before the first paid call, and a machine that cannot reach `openrouter.ai` is refused with
  the host named rather than started.
- A turn's `cost_usd` can be the vendor's bill rather than quackd's arithmetic. The `price`
  record still says the rate, and `billed: true` says which calls it did not decide. A run's
  total can now become null partway, which it could not before.
- The guide's UNVERIFIED table lists what rests on OpenRouter's word alone, the default row's
  tool calls through Chat Completions first among them. **No OpenRouter model has answered a real
  quackd request.** What ran was the real `openai` SDK through a real `quackd run` against a
  stand-in on 127.0.0.1 (`tests/fake_openrouter.py`), the browser demo's client under Node against
  the same stand-in, and the model list check against OpenRouter's real public list, with no key.
