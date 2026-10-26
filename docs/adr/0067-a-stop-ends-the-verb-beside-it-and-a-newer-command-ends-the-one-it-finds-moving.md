# ADR-0067: A stop ends the verb beside it, and a newer command ends the one it finds moving

**Status:** accepted · **Date:** 2026-10-26 · Amends [ADR-0054](0054-a-reflex-may-look-through-a-decision-llm.md) (a stop asked for from beside a verb ends `move`, `go_to`, `search_scan` and `approach_and`, not `watch` alone) and [ADR-0012](0012-safety-executor.md) (the executor can end every verb moving the body for a newer command, and refuses one admitted before it that has not started) · Implemented in `quackd/verbs/core.py` (`_twist`, `_Interrupted`, `_ended_by`, `move`, `search_scan`, `approach_and`), `quackd/verbs/registry.py` (`STOP_ENDED`) and `quackd/safety.py` (`Executor.preempt`, `Preempted`, `_run_admitted`, `_record`) · Documented in [docs/guides/drive.md](../guides/drive.md#one-mover-at-a-time) and [docs/guides/mcp.md](../guides/mcp.md)

## Context

The executor counts every stop asked for, from any caller, and gives each verb a context whose
`interrupted()` says so ([ADR-0054](0054-a-reflex-may-look-through-a-decision-llm.md)). Only
`watch` read it. A stop asked for over MCP while a `move`, a `go_to` or a `search_scan` ran sent
its stop, and the verb's next twist, a tick later, walked the body on: the brake held for a tenth
of a second. A drive session ([ADR-0065](0065-a-person-drives-one-robot-a-command-at-a-time.md))
needs two things more. It brakes beside a running verb on every empty Enter, and it takes a new
command while the last may still be moving the body. "turn left" typed over a walk has to be a
turn, not a turn after the rest of the walk. The one way the executor could end a moving verb for
somebody else was a pause granted to a robot met (`pause_for`), which then holds the body still
for its seconds.

## Decision

1. **A verb that steers reads the stop before every twist it sends.** `_twist` reads
   `interrupted()` before it sends, and `go_to`, `search_scan`'s turns and `approach_and` steer
   through it. `move` reads it before each resend, `search_scan` before each look in both of its
   modes, and `approach_and` between its walk and its `then` verb, so a stop asked for after the
   walk arrived no longer kicks anyway. Each ends through `_ended_by`: the body stopped once more
   after the last twist, and a failed result saying why and how far it got.
2. **A braked verb is not a failing one.** Its result is marked `ended_by: stop`, and
   `abort_when`'s count of failures in a row passes over it, as it passes over a granted pause,
   so braking a walk three times is not the same verb failing three times.
3. **`Executor.preempt(reason)` ends every verb moving the body now**, for a newer command. It is
   `pause_for` with no hold after it. It cancels each verb before its next step, so nothing it
   would have sent goes out after `preempt` returns. A verb it ends is answered with a stop
   after it, a `preempted` gate record, and `Preempted` in the newer command's words. That is a
   `SafetyStop` whose outcome is `preempted`, the flock's word for a member it redirected, and
   being raised, it is never a failure `abort_when` counts.
4. **A verb admitted before a preempt does not start after it.** One already past its gates and
   still reading the body's state is in nothing `preempt` can cancel, and would start after the
   command that replaced it. So `run_verb` counts the preempts before its gates, and a verb that
   may move the body is refused, before it sends anything, once the count has changed.
5. **A read runs on beside either**, as it runs on beside a pause, and `stop` is never in their
   way.

## Why not

- **The stop alone, sent from beside the verb.** The verb's next twist overrode it a tick later,
  which is the bug this ADR exists for.
- **A pause of zero seconds for a newer command.** The pause's record and its result say a robot
  met asked for it, and the person typing would read that a duck nearby had paused theirs.
- **Waiting for the verb to end.** A walk of ten seconds would hold a turn typed in its first.

## Consequences

- `serve-mcp` gains the first half: a `stop` called while a `move`, a `go_to` or a
  `search_scan` runs ends it before its next twist.
- A verb an adapter brings reads the stop beside it only where the adapter asks, as MOSS's
  verbs do. The arm's joint moves do not yet. Drive's brake and a newer command end every verb
  that moves the body through `preempt`, which cancels it whatever it reads.
