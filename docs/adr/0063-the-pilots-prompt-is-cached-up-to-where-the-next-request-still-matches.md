# ADR-0063: The pilot's prompt is cached up to where the next request still matches

**Status:** accepted · **Date:** 2026-10-26 · Amends [ADR-0010](0010-providers.md) (the Anthropic request now marks blocks for the prompt cache: its system prompt, and at most two blocks of its history) and [ADR-0041](0041-the-record-says-when-it-ran-and-what-it-cost.md) (the cache buckets it prices are no longer empty on Claude) · Implemented in `quackd/agent/loop.py` (`_with_cache_point`, and `_history_for_provider` and `_last_invalidated` taking the history to build from), `quackd/agent/providers/base.py` (`Exchange.cache_points`) and `quackd/agent/providers/anthropic.py` (`_system`, `_marked`, `render_messages`, `CACHE_ENV`)

## Context

Rok asked on 2026-10-25 for a command line that answers as close to real time as it can, and
the arm run in the README says where its seconds go: 62.1 of 78.8 waiting on the model, over ten
calls. Every one of those calls sent the whole request again at the full input rate. The system
prompt and the tool schemas are the same on every call of a run, and most of a long run's
history is the same from one call to the next, but nothing quackd sent asked Anthropic to cache
any of it, so no Claude call could read anything back, and the cache rates in the Claude rows of
the catalogue priced nothing.

Anthropic caches only what a request marks, with at most four breakpoints. A read looks back
twenty block positions at most from a marker, a run of tool_use or tool_result blocks counting
as one, and a prefix under the model's minimum, 512 tokens on Opus 5.5, is never cached. The
obvious marker, one at the end of each request, is the one quackd cannot use: the loop trims its
history on every call, on most models dropping the oldest picture still sent, and on Opus 5.5
and Fable 5.1 every eighth exchange, when the pictures go and the thinking the trim invalidates
is left out (`BINDING_TRIM_PERIOD`). Past the first block a trim changes, the next call reads
nothing back, and the write it paid for was a quarter more than the input it replaced.

## Decision

1. **The system prompt is one marked text block.** Anthropic orders a request tools, then
   system, then messages, so one breakpoint there caches the tools and the prompt together, the
   rules, the verbs, the datasheet and the task. The prompt is built once a run, so the marked
   block is byte for byte the same on every call. It is the cache's own five minutes, the
   lifetime every catalogue write rate is priced at, and longer than any pause between two turns.
2. **The loop foresees the next call by the rule that builds every call.** It owns the trim, so
   it does not guess: `_with_cache_point` asks `_history_for_provider` for this call's history
   with its pending exchange answered and one more after it, and marks the last block before
   the first exchange that changes. That is the end of what the next call sends exactly as this
   one does.
3. **The previous call's marker goes out again beside the new one** whenever the two differ.
   Around a trim the marker jumps further than twenty blocks, forward after it and back before
   the next, and a read past the lookback finds nothing, so the entry the last call wrote is
   marked again where the next call reads it. Two markers in the messages and one on the system
   prompt, under Anthropic's four.
4. **The marks ride on a copy.** `Exchange.cache_points` names the halves of an exchange to mark
   on the copy a request is built from, never on the run's history, and `render_messages` puts
   the marker on a copy of the last block of each half that can carry one: never a thinking or a
   redacted thinking block, and never on a replayed raw block itself.
5. **Anthropic's binding check ignores the markers.** Its check of a replayed thinking block
   ignores cache_control markers, added, moved and removed alike, so the binding oracle compares
   requests with them stripped, as the API does, and still passes on every body.
6. **A wrong guess costs a write or two and never a wrong request.** A model that answers in
   prose is asked again rather than answered, which the foresight does not see coming: the
   re-prompt adds two exchanges where it counted one, so the trim's cut can move by both, and
   the entries that call and the next one write can go unread. A retry resends the same marked
   copy, so the request ADR-0059 asks again is the request that failed.
7. **`QUACKD_ANTHROPIC_CACHE=0` marks nothing.** Neither the system prompt nor the history, the
   request exactly as it was before.

## Why not

- **A marker at the end of every request, or Anthropic's automatic caching, which puts one
  there.** The next trim changes the history before it on most calls. 32 of the oracle's 48
  cases fail with that marker, every case of the two bodies with pictures and no prose turn among
  them.
- **The one-hour cache.** Its write is twice the input rate against a quarter more, and the
  turns of a run are seconds apart.
- **OpenAI's `prompt_cache_key`.** OpenAI caches a repeated prefix without being asked, and one
  run is one prefix already, so a key would add nothing to it.
- **Marking in the provider alone.** Only the loop knows which exchange the next trim changes,
  so the provider is told where, and keeps only the say over whether a run caches at all.

## Consequences

- From a run's second Claude call on, the part of its request that matches the last call is
  meant to be billed at the model's cache read rate, a tenth of its input rate on most Claude
  models, a twentieth on Opus 5.5 and a fortieth on Fable 5.1, and written once at a quarter
  more. How much of a run's bill that saves is not measured. The check is one paid run per vendor
  with the cache on and with `QUACKD_ANTHROPIC_CACHE=0`, reading `cache_read_tokens`, `cost_usd`
  and `latency_s` per call.
- A run of one call pays the write and never reads it.
- The test is a request-level oracle through the real loop and the real Claude provider, over
  four bodies, keeps 1 to 3 and trim periods 1, 2, 4 and 8: the system prompt marked the same on
  every call, never more than four breakpoints, every call marking the last block it shares with
  the next, and the next carrying a marker that reaches back to it within the lookback. All 48
  fail with no marker in the messages, and 32 of them with one at the end of each request.
- The interaction with `drop_block` on Opus 5.5 and Fable 5.1 comes from Anthropic's guidance,
  and no paid request has proved it yet.
