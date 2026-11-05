# ADR-0016: The flock clock is lockstep

**Status:** accepted, amended · **Date:** 2026-08-31

**Amended 2026-09-13 by [ADR-0034](0034-registered-robots-and-pilot-flocks.md):** this is
the *auction* flock's clock. A pilot flock has no `FlockClock`: its members are real or
simulated bodies on their own clocks, running concurrently in wall-clock time, so a slow LLM
costs real seconds there and a seed does not make the run reproducible. The "it may return as
a realtime mode" line below is what happened, for that kind only. Everything here still holds
for an auction, which is still the only flock with one shared world to advance.

**Amended 2026-09-26:** a `sleep` now clears only its own waiter on the way out. It used to
clear whatever waiter sat under its id. A task runs that cleanup only when the event loop
resumes it, which can be after the clock has let go of the id: the advancer marks a due
participant awake before it resolves the future, a closed live window marks every sleeper
awake, and `unregister` drops the id. A second task sleeping under the same id could park in
between, because nothing was parked there any more. The first task's cleanup then cleared the
second one's waiter, so that future never resolved and the second task's call hung with no
error to say why. The world paused only as it does while any participant is awake, until
something slept under that id again. `sleep` still refuses a second task while the first is
parked. The cartoon's transport and microduck's MuJoCo one sleep every task under the body's
one id, so two MCP calls in flight could reach this gap. `tests/test_flock.py` builds the
interleaving step by step on each of those paths, and checks that a cancelled sleep still
frees its id. Nothing else in this ADR changes.

**Amended 2026-09-27 by [ADR-0047](0047-the-arms-simulator-runs-the-real-backend.md):** the
arm's simulator runs this clock for one body and many tasks, where an auction runs it for many
bodies. Its `SimClock` gives every sleep a participant of its own, a fresh id numbered in the
order the sleeps arrive, registered for that sleep and unregistered as it wakes. So time runs
while every sleeper is parked, stands still while nobody sleeps, and two tool calls over MCP
sleeping at once both wake, where one id for the whole arm would have had the second refused
and one gate held by a single task would have frozen the rest. A sleep of zero never reaches
`sleep` here, which registers the id and returns without letting it go. The amendment above
was found while that clock was planned. Nothing here changes for the auction.

## Context

In a solo sim run, whoever calls `transport.sleep()` steps the world. With N concurrent
member tasks that breaks: time would advance once per sleeper, in racy order, and
determinism under a seed (the repo's core promise) would be gone.

## Decision

`quackd/sim2d/clock.py` (`FlockClock`): sim time is a shared resource governed by a
barrier.

- Participants park with an **integer remaining-step count** (`round(seconds/DT)`),
  exactly reproducing the single-duck step arithmetic with no float drift.
- An advancer task steps the world one DT at a time **only while every registered
  participant is parked**, fires tick hooks per step, and wakes due sleepers in sorted
  participant order (deterministic).
- The world **freezes while anyone is awake**, so LLM latency costs zero sim time and the
  per-duck deadman keeps exactly its solo semantics.
- `unregister` (called by `transport.close()` and on member exit) cancels a parked future
  and re-evaluates the barrier, so a dead duck can never wedge time. Rule for
  participants, enforced by convention and tests: every await bottoms out in
  `clock.sleep`, and you unregister when you finish.
- The coordinator registers as participant `"coordinator"` with 0.05 s ticks, so bid
  windows, leases and the watchdog run on sim time.

The free-running alternative (a background task stepping continuously with command
latching) was analysed and rejected for v0.3: sim behaviour would depend on wall-clock
LLM latency, killing per-seed reproducibility, and there is no principled headless step
rate. It is the honest hardware-shaped model and may return as a `realtime` mode.

## Consequences

- `n_ducks=1` runs are bit-identical to the pre-flock code (verified by the unchanged
  seeded acceptance suite); flock runs with the fake planner are reproducible per seed.
- Wall-clock `Heartbeat` tasks interleave freely (the advancer yields to the loop every
  few steps); only failure-path timing is wall-clock dependent, as in solo runs.
- One subtlety, covered by tests: a participant that wakes and neither sleeps again nor
  unregisters freezes time for everyone. Members' idle roles are sleep loops on purpose.
