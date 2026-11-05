# ADR-0015: Flocks are a deterministic coordinator with one planner call

**Status:** accepted, amended · **Date:** 2026-08-31

**Amended 2026-09-13 by [ADR-0034](0034-registered-robots-and-pilot-flocks.md):** the "Out
of scope for 0.3" line below says per-duck LLM pilots and LLM-negotiated bids are not built.
They are built now, as a *second kind* of flock rather than a replacement for this one:
`flock.allocation.method: pilots` runs one `AgentLoop` per member on wall-clock time with a
`tell` tool over this ADR's own bus, and `auction` is still the default and still everything
decided here. The reasoning above is untouched and still correct for the task class it names:
a Contract Net is near-optimal for ST-SR-IA, and pilots do cost N times the tokens and the
latency, which is why the kick demo still runs the auction. What changed is that
[ADR-0032](0032-datasheets-and-the-verdict.md) gave a pilot a datasheet to reason from, so two
pilots splitting a task between two different bodies are reading data rather than improvising,
which is a task class this coordinator cannot express at all.

## Context

v0.3.0 adds cooperating ducks ("find a ball, the closest one kicks"), following an
accepted research report that surveyed RoboCup task allocation, the MRTA taxonomy and the
LLM multi-robot literature. The task is ST-SR-IA (single-task robots, single-robot task,
instantaneous assignment), the simplest allocation class, where auctions are near-optimal
and cheap. The report's verdict: hybrid topology, LLM out of the hot loop, market-based
claim with hysteresis.

## Decision

- The `flock:` block lives in the `.duck` frontmatter (one artifact stays the product;
  older quackd versions reject flock ducks loudly via `extra="forbid"`, which is correct).
- Coordination is a **deterministic Contract Net auction**: bid = the duck's own camera
  ball-distance estimate, 0.4 s sim bid window, tie-break by member name, 20 % hysteresis
  for the previous kicker, 6 s claim lease, cooldown on failure, one-claimant lock,
  full-circle re-search after a miss (the ball moved, wedges are stale).
- Members are **role FSMs, not agent loops**: zero LLM calls; every verb goes through that
  duck's own `Executor` (allowlist, budgets, abort rules, dry-run). `FlockPreempted`
  subclasses `SafetyStop` so a role change ends a verb without counting as a failure.
- The LLM makes **at most one** `plan_flock_task` call (validated, clamped, fallback to
  deterministic defaults); wedges are always computed deterministically; the fake provider
  makes zero calls. `summary.json` records `llm_calls` as proof.
- The bus is in-process only, synchronous fan-out, never awaited (a participant blocked on
  a queue would freeze the lockstep clock); the `Bus` protocol is the seam for MQTT later.
- The **outcome is ground truth** from sim telemetry (total `ball_displacement_m`), never
  an LLM claim; a rally of short kicks counts because the contract criterion is total
  displacement.
- Safety separation uses world ground truth (documented); the kicker's approach uses
  perception only. The sighting quack is theatrical, as the report recommends.

## Out of scope (v0.3, stated in docs)

MQTT/LAN bus, hardware flocks, per-duck LLM negotiation, overhead-referee localization,
AprilTags, UWB, acoustic data channel.

## Consequences

- Cooperation is inspectable: every message, bid and claim is one line in `flock.jsonl`,
  and tests replay the log to prove the one-claimant invariant.
- Scripted 3-duck `find-and-kick`: 10 of 10 seeds, 0.4-3.3 s wall each.
- When upstream ships a LAN story, only a `Bus` implementation and a transport change.
