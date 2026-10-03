# ADR-0043: Decision LLMs are a wire format and a data row, not a vendor

**Status:** accepted, amended · **Date:** 2026-09-22 · Extends [ADR-0040](0040-a-discrete-stepper-in-front-of-the-model.md) (the stepper, its floors and its gates, all unchanged) and borrows the discovery pattern from [ADR-0037](0037-adapters-are-their-own-packages.md) (adapters announce themselves through an entry point group) · Amends [ADR-0040](0040-a-discrete-stepper-in-front-of-the-model.md) and [ADR-0041](0041-the-record-says-when-it-ran-and-what-it-cost.md) (their Jev-specific spellings) · Retires `--jev` outright, with none of the release of grace [ADR-0042](0042-the-log-is-the-whole-screen.md) gave the trace spellings · Also merges `--provider` and `--model` into `--llm`, amending [ADR-0031](0031-model-catalogue.md) · Implemented in `quackd/agent/decision/` (was `quackd/agent/jev.py`) ([page](../guides/decision-llms/README.md))

**Amended 2026-09-22:** "a section on the page" under Consequences is a page now. Each preset
has `docs/decision-llms/<name>.md`, in the shape of the adapter pages, with a VERIFIED and an
UNVERIFIED section and a status line that stays until one has answered a real robot, and the
hub's table links it. `docs/decision-llms.md` itself did not move, so nothing that links it
had to.

Two sentences below are wrong on their facts and the decisions above them are not. Read
"it reports no token count at all" about `laya` as its opposite: `laya/agent.py` returns
`usage.input_tokens` as a real tokeniser count, so it is the one row billed measured, and it
is priced at the self-hosted rate because of where it runs rather than because of what it
counts. And read "the floors are Jev's numbers" as true of two of the four: TypeSafe publish
0.5 and 0.9, which are the brake and the confirm gate, while the 0.60 read floor and the
0.85 motion floor are quackd's own, set between them. Nothing else in this ADR changes.

## Context

[ADR-0040](0040-a-discrete-stepper-in-front-of-the-model.md) is four days old and its
reasoning has held up completely. What has not held up is a single word in it.

That ADR decided quackd should put something in front of the model on the turns whose answer is
a choice, and it named that something TypeSafe's Jev, because on 2026-09-18 Jev was the only
one there was. Jev launched on 2026-09-15. Within the week some thirty projects had shipped
models that answer the same typed questions, most of them open, several small enough to run on
the laptop this is being written on, and at least five serving the identical HTTP contract on
purpose. `systemonemodels.org` lists them; the count was 33 when this was read on 2026-09-22.

quackd had spelled one vendor's name into a CLI flag (`--jev`), a pip extra (`quackd[jev]`),
three environment variables, a module (`quackd/agent/jev.py`), two transcript kinds, five
record fields, a summary block, three terminal labels, a doctor row and a 603-line page. None
of those spellings was wrong on the day. All of them were about to become wrong, in the way
that is hardest to undo later: quietly, by being copied.

The thing every one of those projects shares is not a vendor. It is a wire format — `POST
/v1/systemone`, a named state and a mapping of typed questions in, answers with probabilities
out — and the instruction each of their READMEs gives is the same sentence: point TypeSafe's
own client at our base URL.

## Decision

**The wire format is the abstraction.** One HTTP backend, `decision/systemone.py`, reaches Jev,
Kev, Von, OpenJev, OpenDecision and anything else that speaks it. The questions quackd builds
are plain dicts in the System One shape rather than an SDK's own `Choice` and `Noul` objects,
so the same four questions go to a hosted API, to a server on this machine and to a model in
this process without being rebuilt for any of them.

**A decision LLM is a row of data.** `decision/catalogue.py` holds name, backend, default URL,
default model, key variable, price and install hint, and imports nothing. Adding a
wire-compatible server is one row; a server quackd has never heard of is `--decision-llm local
--decision-url` and no code at all. The split between a data module and a factory that imports
is the one `adapters/catalogue.py` already makes, and for the same reason: `quackd doctor` has
to render the whole table on a machine where none of them is installed.

**The row is the only place a per-vendor fact may live.** This is worth stating as a rule
because the table proved it within a day of being written. OpenJev accepts a closed set of
model ids and refuses a pinned Jev version with a 400; Laya's plain name is an alias for its
English checkpoint rather than its decision-tuned one; Kev's own code defaults to port 8008
while every line of its README passes 8009. A shared default would have been wrong for each of
them, in a different way, on every request.

**In-process decision LLMs sit behind the same protocol.** `decision/laya.py` is the proof that
the seam is a protocol and not an HTTP call: Laya takes the same question mapping the wire does,
and everything quackd asks of a hosted model it asks of this one. It is also the case that
keeps the billing honest, because it reports no token count at all.

**Third parties announce themselves.** The `quackd.decision_llms` entry point group, discovered
the way `quackd.adapters` is discovered, with the same `find_spec` verification and the same
refusal to believe stale metadata. A built-in name always wins over a plugin that took it, so a
package on an index cannot quietly become the thing `--decision-llm jev` reaches.

**`typesafe_sdk` stays the transport for every server.** It is the protocol's reference client:
it owns the retry policy, the typed errors and the `int | None` usage the billing already reads
tolerantly, and every compatible server documents itself against it. The extra that installs it
is called `decision` rather than `jev` or `typesafe`, because what it installs is a protocol
client.

**No plain-LLM fallback.** An adapter that asks GPT or Claude for a JSON object of
probabilities exists and would have been easy to add. Those numbers are prompted rather than
calibrated, and quackd's floors gate real motion on calibration: `motion` is 0.85 and `confirm`
is 0.90 precisely because the number underneath them is meant to mean something. Anybody may
write it as a plugin. They would have to measure it first, and so would we.

**The grammar mirrors the pilot's.** `--llm VENDOR[:MODEL]` and `--decision-llm NAME[:MODEL]`,
`--base-url` and `--decision-url`. The third decision flag is a mode rather than a model,
because the spec already carries the model and because `shadow` is the flag that matters: it is
how you find out whether to trust one of these before you do. Naming a decision LLM defaults
the mode to `on`, so the common case is one flag; a mode with nothing named is refused rather
than silently doing nothing.

**A clean rename, with no release of grace.** [ADR-0042](0042-the-log-is-the-whole-screen.md)
gave the trace spellings a release, and was right to: they were three releases old and in
people's scripts. These are three days old, `quackd record` pinned the stepper off so no
recording in this repository carries them, and ADR-0040 already promised that a transcript kind
the renderer does not know is drawn as nothing. So `--jev` is gone rather than deprecated, and
`quackd log` on a 0.10 or 0.11 run directory prints no stepper lines, still prints `from jev`
on the verbs it chose, and shows no stepper seconds or cost in its counters. The run directory
is untouched; only the reading of it is.

**The floors are Jev's numbers, and every other preset inherits them unmeasured.** ADR-0040
took them from TypeSafe's published guidance, which was the honest thing to do for a model
quackd cannot tune against. It is less honest the moment there are seven of them, because each
one computes confidence by its own formula. The floors do not move here, and the warning on
`--decision-mode on` now says exactly this.

### And the pilot flag

`--provider anthropic --model claude-opus-5` became `--llm anthropic:claude-opus-5`, with
`QUACKD_MODEL` becoming `QUACKD_LLM` and a robot's two registry fields becoming one. This is
not really a separate decision: the two flags were never independent, since a model id means
nothing without its vendor and every refusal had to name both anyway. What forced it now is
that `--decision-llm` needed a grammar, and shipping a new pair `--decision-provider` plus
`--decision-model` while the old pair was already awkward would have been choosing the wrong
half to copy. A bare catalogue id infers its vendor, because the ids are unique across vendors
and a test holds them to it.

The internals keep the word provider: `LLMProvider`, `make_provider`, and the `provider` and
`model` fields in every record. The flag is a grammar; the abstraction underneath it did not
change. `robots.json` is user data nobody re-saves on upgrade, so an old file's `provider` and
`model` keys are folded into `llm` on read.

## Why not

**A module per vendor.** It is what the providers do, and it is right there: eleven vendors,
one file each. But a provider is a real abstraction with a real surface — tools, streaming,
images, thinking — and these are one POST with the same body. Eleven files that differed only
in a URL and a string would be a table written in Python.

**Ollama.** The obvious guess, and wrong. Ollama serves chat models; none of these
architectures — LoRA heads, pointer heads, masked encoders — run in it, and its
OpenAI-compatible endpoint drops logprobs, which is the one thing a decision would need.
It remains what it already is: a pilot preset for `--llm`.

**OpenRouter's Decisions API.** It serves Jev at `/api/alpha/decisions`, and the SDK's path is
a hardcoded constant with an open issue asking for it to be configurable. Until that lands it
cannot be reached by repointing a base URL, which is the whole mechanism here.

**A SemIf adapter.** SemIf is the most interesting of the open reproductions and it does not
fit: a batch CLI over a JSONL file, choice-only, its own input schema, no server, and the model
loaded per invocation. It is a good tool for a different job. Wrapping it would mean writing a
server, which anybody can do as a plugin.

**Keeping `--jev` as a hidden alias.** Three days, and `record` pinned it off. An alias would
have outlived the thing it aliased.

## Consequences

- Adding a wire-compatible decision LLM is a row in `decision/catalogue.py` and a section on
  the page. Adding one with its own Python API is a plugin nobody here has to review.
- A run's record now says which decision LLM answered, not only which model id, because
  `kev-latest` on two machines is two different servers. `run_start` carries
  `decision_llm` beside `decision_price`, and the summary block carries `llm` and `url`.
- A URL can carry a credential and arrives from an environment variable that argv redaction
  never sees, so it is written through `redacted_url` where the stepper is built rather than at
  each place that records it.
- A server you run is costed at the self-hosted rate the rest of quackd already uses for a
  local model: `$0`, and not `None`, which quackd reserves for "there is a rate and nobody here
  knows it". Only Jev has a published rate.
- `quackd doctor` gains a row per preset with its address. It does not probe any of them: the
  providers table does not probe cloud vendors either, and these do not all expose a listing
  endpoint on the same path or in the same shape.
- The 0.11.0 README on PyPI links to `docs/jev.md`, which has moved. That link 404s until the
  next release replaces the README.
- Nothing here has been run against a real robot, and the client has been exercised only
  against a stub. `--decision-mode shadow` is the way that changes, and it is what the `on`
  warning points at.
