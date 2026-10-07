# ADR-0041: The record says when it ran and what it cost

**Status:** accepted, amended · **Date:** 2026-09-21 · Extends [ADR-0029](0029-tracing.md) (one event stream, and what it records) and [ADR-0031](0031-model-catalogue.md) (the catalogue is where model data lives) · Amends [ADR-0040](0040-a-discrete-stepper-in-front-of-the-model.md) (the stepper's summary block, and its consequence that the speed and cost figures in `docs/jev.md` are an estimate) · Implemented in `quackd/agent/providers/pricing.py`, with changes in the loop, the stepper and both console views

**Amended 2026-09-22 by [ADR-0042](0042-the-log-is-the-whole-screen.md):** `quackd trace` below
is `quackd log`. The old spelling still runs, hidden from `--help`, prints one yellow line
naming the new one, and goes in 0.12. The record this ADR adds to is the log from that release
on, and it carries the terminal session as well. Nothing else in this ADR changes.

**Amended 2026-09-22 by [ADR-0043](0043-decision-llms-are-a-wire-format-and-a-data-row.md):** the stepper's record fields were named for one vendor and are
now named for the format. `run_start.jev_price` is `decision_price`, with a `decision_llm`
beside it saying which one answered and where; the kinds `jev` and `jev_shadow` are `decision`
and `decision_shadow`, and the five `jev_*` fields on the shadow record are `decision_*`; the
summary's `jev` block is `decision` and carries `llm` and `url`; `QUACKD_JEV_PRICE` is
`QUACKD_DECISION_PRICE`. Read "TypeSafe" in the stepper's costing below as "whichever decision
LLM answered": a server you run reports no bill and is costed at the self-hosted `$0` the rest
of quackd already uses for a local model, so the estimate and its `~` exist only where a
non-zero rate does, which today is Jev alone.

**Amended 2026-10-06 by [ADR-0050](0050-a-router-is-a-vendor-whose-list-is-read-on-the-day.md):** a call can be costed at what
the vendor says it billed. On OpenRouter a turn's `cost_usd` is its `usage.cost`, with the
upstream charge added on a bring-your-own-key call, unless `--price` or `QUACKD_PRICE` named a
rate, and that `llm` record carries `billed: true`; the summary counts such calls in
`billed_calls`. The `price` record is still the rate, which is what a call with no bill is
costed at, and a rate read on a day of its own carries that day in `checked`. A run's total
becomes null from the first call that has neither a bill nor a rate. Every other vendor's
records are unchanged.

## Context

The one hardware run this project has is at the top of `README.md`, and the sentence that
matters most about it is this one: *"Ten model calls, 3.3 to 8.2 seconds each, 62.1 seconds in
all, which is 79% of the run. The arm moved for 12.2 seconds, which is 15%."* That 62.1 was
added up by hand, out of the `latency_s` on ten `llm` records, because nothing in quackd
totalled it. `summary.json` gave `steps`, `llm_calls`, a token count and `elapsed_s`, and
`elapsed_s` is the budget's clock rather than the wall's.
[ADR-0040](0040-a-discrete-stepper-in-front-of-the-model.md) is built on that ratio and
`docs/jev.md` quotes it three times, so the number this project's newest argument rests on was
the one number a run did not record.

Money was worse, because it was declared out of scope. `docs/jev.md` line 494 says **"quackd
counts tokens and has never computed money"**, and the rest of that section is arithmetic the
reader is expected to do against a rate card they first have to go and find. Meanwhile
[ADR-0031](0031-model-catalogue.md) had already decided that what quackd knows about a model
belongs in one curated table, and that table held 115 models without the one field anybody
converts a token count into. A reader looking at `in=10730 out=96` cannot tell a ten cent run
from a ten dollar one without a browser open beside the terminal.

Absolute time was missing in the same way. Until now the only wall clock reading a run had was
the stamp in the name of its directory: UTC, to the second, and evidence only for as long as
nobody renames the folder, copies it off the bench machine or pastes its contents into an
issue. Inside `transcript.jsonl` every `t` is seconds since the record opened, which is the
right shape for reading a run back and no use at all for saying *when* it happened or for
lining a run up against a bench log, a camera recording or a colleague's note.

And a run had no name. A bench session of a hundred hardware runs is a hundred directories
that differ only in a timestamp nobody wrote down. The SO-101 afternoon was twelve of them, and
the honest way to find the wave afterwards was to open transcripts until the goal matched.
`--run-name "Example 1"` is the answer to that, and it has to reach the directory name, because
the directory name is what somebody types back into `quackd trace`.

## Decision

- **One wall anchor per run, and every record keeps its relative `t`.** The transcript reads
  `started_at` in the same breath as its monotonic `_t0`, and that pair is the only place the
  two clocks meet. `run_end` derives `ended_at` as `started_at + wall_s` rather than reading
  the clock a second time, so `ended_at - started_at == wall_s` holds of every record quackd
  writes and no reader ever has to decide which of two stamps to believe.

- **`wall_s` is the honest start to finish figure, and `elapsed_s` is left exactly as it was.**
  `elapsed_s` is `safety.Budget`'s clock, which is the transport's own, and on `sim2d` that is
  the simulator's: a verified run of `find-and-kick` recorded `elapsed_s` 7.8 against `wall_s`
  0.203, because the simulator stepped ahead of the wall. Making those two agree would break
  the budget line that appears in every observation and that `quackd trace` parses back out.
  So there are two clocks and each is named for what it measures: `wall_s` for how long a
  person waited, `elapsed_s` for how much of the budget the robot spent. `connect_s` and
  `llm_latency_s` split the wall into the parts worth arguing about, and `llm_latency_s` is
  summed over every model call including one that raised, because a call that failed slowly
  still cost the run its seconds.

- **A run can be named, and the name is validated before anything else happens.**
  `--run-name` is recorded as typed and goes on disk as a slug, after the duck name and before
  the collision counter, giving `20260921-155822-find-and-kick-example-1`. `quackd trace`
  gained one resolution pass for it, ahead of both the timestamp prefix and the loose
  substring, matching the slug against what follows the stamp, so `quackd trace example-1`
  finds `example-1` and not the newer `example-19`. Ahead of the prefix pass rather than
  behind it, because a label can be all digits and a run called `20260921` was otherwise
  answered by whichever run's timestamp started the same way; and only where a duck name
  precedes the label, because otherwise a bare duck name matched an unnamed run of that duck
  and quietly preferred it to a newer named one. A name with nothing in it to slug is an error and
  not a fallback, and both it and `--price` are checked at the top of `_run_impl`, before
  anything connects and before a directory exists: a bench session that mistyped a flag should
  find no run rather than an unnamed one.

- **`Usage` is billing buckets, and `input_tokens` is the whole prompt.** The convention is now
  written down on the model and every adapter converts into it: `input_tokens` is every prompt
  token the request consumed, cached parts included; `cache_read_tokens` and
  `cache_write_tokens` are slices of that, not additions to it; `output_tokens` is everything
  generated, thinking included, because that is what the output rate is charged on; and
  `reasoning_tokens` is the slice of the output the vendor counts apart, recorded and never
  priced a second time. A number that means one thing on Anthropic and another on Gemini
  cannot be summed across a run or multiplied by a rate, which is the whole reason the
  normalisation lives in the adapters rather than in the arithmetic.

- **Rates live in the catalogue, `--price` and `QUACKD_PRICE` override them, and unknown is
  recorded as `null`.** A rate is model data, so by [ADR-0031](0031-model-catalogue.md) it
  belongs in the same table as the id and the vision flag: 112 of the 115 models carry one,
  read off the vendor's own page on `PRICES_CHECKED`. `None` means the vendor publishes no
  per-token rate, never that a model is free; a genuinely free model is `Price(0.0, 0.0)`, and
  four of them are. An override is a flag or a variable rather than a second table because the
  cases are personal: a negotiated rate, a paid endpoint behind a local preset, a model the
  catalogue has never heard of. An override beats `fake` too, which is how the whole cost path
  gets exercised end to end with no key and no bill. Where a cache rate is unpublished,
  `cost_usd` charges that traffic at the full input rate, because a bill that is too low is the
  one that gets believed.

- **The stepper's cost is measured from the API's own count, estimated only when it reports
  none, and always flagged.** TypeSafe's SDK types both token counts `int | None`, so `_bill`
  reads what came back and falls back to `(state_chars + question_chars) // 4`, which is the
  arithmetic `docs/jev.md` was doing by hand, setting `usage_estimated` on the record and
  `cost_estimated` on the summary when it does. A call that raised after the request went out
  is billed on the estimate, because the request was sent; the gates that never reach the
  network, `not_offered` and `state_too_large`, add nothing at all, so a machine with no
  `typesafe_sdk` installed owes nothing. The counters wear a `~` when any turn was estimated,
  because an estimate a reader cannot tell from a measurement is worse than no number at all.

- **No new event kinds, only fields on the ones that exist.**
  [ADR-0029](0029-tracing.md) made the transcript the record and promised existing kinds keep
  their exact shape. So `llm` gained `cost_usd` and `cost_usd_total`, `jev` gained its usage
  and cost, `jev_shadow` gained both figures, `run_start` gained `run_name`, `started_at`,
  `price` and, only where a stepper ran, `jev_price`, and `run_end` gained its clocks and its
  money on top of every key it already had. Each new console field renders only when it is
  present, so a transcript written last week replays byte for byte, which
  `tests/golden/log_lines.json` freezes rather than trusts.

- **One summary shape, printed by one function on both surfaces.** `RunResult.summary` is the
  exact dict written to `summary.json`, and `cli.run_counters` builds the counter line from it
  for the live verdict and for `quackd trace` alike, so the two cannot drift into printing
  different arithmetic about the same run. Every field it reads is optional: a summary written
  before this change has no `wall_s`, no `cost_usd` and no `jev` block, and prints exactly the
  three counters it always did.

## Why not

- **A wall timestamp on every record.** It is the first thing anybody reaches for and it is
  redundant by construction: `started_at + t` gives it exactly, for every line, forever. What
  it adds is bytes to a file that already carries two hundred `intent` lines for one twenty
  second `go_to` ([ADR-0029](0029-tracing.md)), and a second authority that can disagree with
  the first when the OS resynchronises the clock mid-run. An anchor plus a monotonic offset
  cannot drift and cannot contradict itself.

- **`input_tokens` meaning only the uncached part.** Attractive because the four buckets would
  then add up to the bill with no subtraction anywhere. It would also silently change the
  meaning of a number that every transcript since the first release carries and that people
  compare across runs, and it would put quackd at odds with two of its three biggest vendors:
  OpenAI and Gemini both report a prompt total that already contains the cached part, so
  quackd would have to subtract on those two in order to add on the third. The subtraction
  happens in one place instead, inside `cost_usd`, where it is arithmetic rather than meaning.

- **Pricing an unknown model at zero.** A missing rate and a free model are different facts and
  only one of them is good news. Cohere is the live case rather than a hypothetical: the
  Command A family is sold as dedicated instances by the hour, so there is no per-token figure
  to record, and the family includes the model quackd defaults to for that vendor. A zero there
  would print `$0` for capacity somebody is paying for by the hour. `null`, a `cost unpriced`
  counter, and one dim line naming the provider and the model and telling you to pass `--price`
  is a worse-looking answer and a true one.

- **Recomputing cost at replay from today's catalogue.** Then a run's cost would change every
  time a vendor moved a rate or quackd fixed a typo in the table, two readings of the same
  transcript a month apart would disagree, and neither would say why. The rate is written into
  `run_start` and `summary.json` instead, with its source and its checked date, so
  `quackd trace` prices a run at what it cost on the day. A rate later found to be wrong then
  shows up as a wrong number in the runs that used it, rather than being quietly reapplied to
  every run in the archive.

- **Estimating the stepper's tokens even when TypeSafe measured them.** One formula everywhere
  is a real virtue: nothing to caveat, nothing to explain, every run comparable with every
  other. It buys that by throwing away a measurement in favour of four characters to the token
  against a tokenizer nobody here has seen, and it makes the figure permanently unfalsifiable,
  because nothing would ever be measured that could disagree with it. Recording which one each
  turn was costs one boolean and leaves the estimate able to be checked and corrected.

## Consequences

**Gemini runs report more output tokens than they did**, and the old number was the wrong one.
Google reports thinking beside the answer rather than inside it, so `output_tokens` is now
`candidates_token_count + thoughts_token_count`. Thinking has always been billed at the output
rate; it was being left out of a number that exists to be multiplied by that rate. Any
comparison between a Gemini run recorded before this and one recorded after is comparing two
different quantities, and only the second one is the bill.

**Anthropic's `input_tokens` now includes its cache buckets.** Anthropic reports its three
input buckets disjoint, so the adapter adds them up rather than passing the uncached remainder
through as though it were the prompt. quackd sets no cache breakpoint on any request today, so
on Anthropic those buckets are zero and this is a correction waiting for the caller who turns
caching on; where a vendor caches by itself the cached slice of the prompt is now recorded and
charged at the cache rate instead of disappearing into the full one.

**Three Cohere models have no published rate, and one of them is that vendor's default.**
`command-a-plus-05-2026`, `command-a-03-2025` and `command-a-reasoning-08-2025` report
`cost_usd: null` and print `cost unpriced`. This is not an omission waiting to be filled in by
somebody diligent, it is what Cohere publish, so no test may assert that every vendor default
carries a price.

**Every rate is only as current as `PRICES_CHECKED`, and the table is flat.** Today that date is
2026-09-21, and it is written into every run that used a catalogue rate so that a figure can
be aged rather than merely doubted. Vendors move rates, and several of them charge in ways
four numbers cannot express: xAI and the Gemini Pro models double above a 200k prompt and
OpenAI's flagships above 272k, repricing the whole request rather than the excess; DeepSeek
halve everything outside their peak hours; batch, priority, provisioned and free tiers are
choices a caller makes that quackd cannot see. The table records the standard on-demand rate
in the short context band, so a long prompt on a banded vendor is under-costed, and each
vendor's own comment names the threshold where that begins. `--price` is the answer for
anybody whose bill does not look like this table, and the `source` written into the record is
how a reader tells the two apart.

**The stepper's cost is a measurement only when TypeSafe reports one.**
[ADR-0040](0040-a-discrete-stepper-in-front-of-the-model.md) said the figures in `docs/jev.md`
are an estimate, and that stays true of the run level projections on that page. What changes is
narrower: where the API returns a token count, the cost in the record is computed from it, and
where it does not, the record says so. Nobody has yet asked TypeSafe's production API for a
count from inside a quackd run. Both branches are exercised against `tests/fake_typesafe.py`,
which reports whatever a test hands it, so the shape is proven and the number is not.
