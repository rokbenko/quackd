# ADR-0012: One executor between every LLM and the transport

**Status:** accepted · **Date:** 2026-08-28 · **Amended 2026-09-05:** gate 1 exempts `stop` (the abort is set exactly when the pilot reaches for the brake), and gate 9 races the abort event as well as the clock, so a kill switch cancels the running verb. `RobotSession` exempts `stop` the same way. **Amended 2026-10-19 by [ADR-0054](0054-a-reflex-may-look-through-a-decision-llm.md):** a verb that runs for a while may ask whether to end (`VerbContext.interrupted`), and is told so once the run's minutes are spent or a `stop` has been asked for from beside it, and a verb the executor cancels is told why: the abort, its timeout, or a caller that went away. Only `watch` asks, and `go_to` does not yet. **Amended 2026-10-20 by [ADR-0056](0056-moss-through-its-cockpit-and-a-bridge.md):** a verb of the `dangerous` class runs only when a person answered its confirm. Under `--yes`, a flock's standing answer, the MCP server or a pipe on stdin nobody is asked, so it is refused there before its question is put, and with no task file it is not allowed at all. **Amended 2026-10-26 by [ADR-0067](0067-a-stop-ends-the-verb-beside-it-and-a-newer-command-ends-the-one-it-finds-moving.md):** `move`, `go_to`, `search_scan` and `approach_and` ask too, so a `stop` from beside one ends it before its next twist, and `preempt` ends every verb moving the body for a newer command and refuses one admitted before it that has not yet started. A verb answers to the stops counted from when it was asked for, the abort is read again after the body's state, so a verb admitted before it never starts after it, and a battery rule that fires never refuses `stop`.

## Context

Two clients drive the duck: the agent loop and the MCP server (Claude Code / Desktop).
Both take instructions from a model. Upstream's `robotd` guards the body; something has to
guard the conversation, identically for both clients.

## Decision

`quackd.safety.Executor.run_verb(name, params, source)` is the only path to
`transport.send_intent`. In order:

1. **Abort flag** — set by heartbeat failure or kill switch → `Aborted`.
2. **Allowlist** — `.duck` `verbs.allow` (or, with no contract loaded, every verb whose
   `safety_class != "dangerous"`). `stop` is always allowed. → `VerbNotAllowed`.
3. **Params** — pydantic validation; errors return a failed `VerbResult` (feedback).
4. **Confirm gate** — `verbs.confirm` or `safety_class in {confirm, dangerous}` → a y/N
   callback (`typer.confirm` in the CLI, `--yes` to auto-accept). → `ConfirmDenied`.
5. **Budget** — `max_steps` counted here; `max_llm_calls` and `max_minutes` in the loop,
   with time from the transport clock. → `BudgetExceeded`.
6. **Machine-enforced `abort_when`** — battery threshold (from state) and consecutive
   failures per verb. → `Aborted`.
7. **Preconditions** — e.g. not fallen, not sitting → failed `VerbResult`.
8. **Dry run** — non-read-only verbs log and return without touching the transport.
9. **Execute** with `asyncio.wait_for(timeout_s)`; timeouts and exceptions stop the duck and
   return a failed result.

`Heartbeat` pings `transport.heartbeat()` every 500 ms; one failure → `stop()` + abort.
`KillSwitch` maps Ctrl-C and `q` to the same abort event (signal handler, not
`loop.add_signal_handler`, so it works on Windows). The loop always sends a final `stop`
and closes the transport in `finally`.

## Consequences

- MCP sessions get the same allowlist and budgets as `.duck` runs (M4 wires the same class).
- The physical rule still stands and is documented: on hardware the gamepad preempts
  remote control; quackd does not try to out-rank it.
- `--dry-run` is a first-class way to read what an LLM *would* do to a robot before
  letting it.
