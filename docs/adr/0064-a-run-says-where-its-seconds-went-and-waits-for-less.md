# ADR-0064: A run says where its seconds went, and waits for less

**Status:** accepted · **Date:** 2026-10-26 · Amends [ADR-0040](0040-a-discrete-stepper-in-front-of-the-model.md) (in `--decision-mode shadow` the stepper is asked beside the model's call, not in front of it), [ADR-0041](0041-the-record-says-when-it-ran-and-what-it-cost.md) (the summary says how the calls spread and when the body first moved, and `run_start` what the pilot was asked to reason at) and [ADR-0059](0059-a-turn-the-api-could-not-answer-is-asked-again.md) (a call is raced against the task's minutes as well as the stop) · Implemented in `quackd/latency.py`, `quackd/agent/loop.py` (`_unless_stopped`, `_minutes_spent`, `_guarded`, `_settle_beside`, `_Warming`), `quackd/safety.py` (`Executor.first_motion_at`), `quackd/agent/transcript.py` (`save_frames_off_loop`), `quackd/agent/providers/sdk_warm.py`, `quackd/agent/providers/factory.py` (`EFFORTS`, `effort_refusal`), `quackd/ui.py` and `quackd/cli.py` (`--effort`, `run_counters`)

## Context

Rok asked on 2026-10-25 for a command line that answers as close to real time as it can. Most
of that is a new way to drive a robot, which comes in later releases. Some of it is `quackd run`
as it stands, where a person waits on four things: the model's calls, which on the README's arm
run were 62.1 of 78.8 seconds; the turn around them; the stepper in shadow mode, asked before
the model on every turn although its answer changed nothing there; and the record, which could
not say how long any of it took a person to see. `summary.json` had one total per clock: nine
quick answers and one that hung added up like ten average ones, and the question a person by
the robot asks first, how long it took to do anything at all, was in no field.

## Decision

1. **The summary says how the calls spread and when the body first moved.** `quackd.latency`
   is the one definition, nearest rank, so every figure is a call that happened. The summary
   gains `llm_latency_p50_s`, `llm_latency_p90_s` and `llm_latency_max_s` over the calls that
   answered, only where one did, and the stepper's block the same three. `first_motion_s` is
   when the first verb that may move the body went to the transport, on the record's own clock
   with the connect in it, and `first_motion_llm_calls` how many model calls came first, the one
   that chose it included. The executor stamps it after every gate, so a refused verb, a dry run
   and a read never count, and both are null where nothing moved. The counters print them only
   where the keys are, so an old record replays with the counters it always had.
2. **A picture is encoded once.** The save that writes `frames/` hands back the bytes it wrote,
   and the model is sent those, so the file a reader opens is the picture the pilot was shown.
3. **The status line says what the answer was.** The call the model chose, that it answered
   without one, or that the call failed, in place of "choosing a verb", which was over by then,
   and a wait carries the last answer's seconds.
4. **`--effort low|medium|high`** on `run` and `preflight` reaches Anthropic's
   `output_config.effort`, OpenAI's reasoning effort and a model server of your own, and beats
   `QUACKD_EFFORT` and `QUACKD_OPENAI_REASONING_EFFORT`, which still stand without it. Three
   levels, because both vendors have those three. It is refused before anything connects where
   it cannot reach: the scripted pilot, `--controller vla`, a model that takes no effort or not
   at that level, by its catalogue row (`ModelSpec.effort` and `effort_levels`, read off each
   vendor's own page), and a vendor that documents no such field. `run_start.effort` says what
   every request asked for and whose word it was, `{value, from}`.
5. **The pilot's client is warmed while the robot connects, and nothing waits for it.** A client
   of either SDK starts its first request by asking which platform it is on, on Windows a WMI
   query or a subprocess, and by importing anyio's asyncio backend. The provider's `warm()` asks
   both in a daemon thread started before the connect's clock, sends nothing and never raises,
   and `run_start.pilot_warm` says what became of it.
6. **A pilot call is raced against the task's minutes.** `max_minutes` was read only once an
   answer came back, so a call that hung held the robot past the task's whole budget for as long
   as the vendor's own clock allowed, ten minutes on both SDKs. A third waiter beside the call
   and the stop reads the budget's own clock every heartbeat period, and once the minutes are
   spent the call is cancelled, its `llm` record says so, it is not asked again, and the run ends
   on its budget. On a simulator that clock stands still while a model thinks, so a simulated
   run is never cut off there.
7. **Shadow asks the stepper beside the model's call.** In shadow the stepper's answer changes
   nothing about a turn, so its request goes out with the model's, from the moment the model's
   call is made, and is settled once the model's answer is on the record: awaited, raced against
   the stop, or cut off on the gate `interrupted` where the model's call ended the run. A Ctrl-C
   that lands in its task is raised in the run's own task once its record is written. In `on`
   the stepper still answers first, since its answer can take the turn.

## Why not

- **Streaming the model's answer.** A mid-stream error arrives inside a 200, which
  `transient_failure` cannot read, and the tool call, the only part a turn acts on, comes last.
- **A fixed ceiling on each call.** It would cut long thinking short and bill the retry twice;
  the task's minutes are the bound the task already chose.
- **Opening the connection early.** httpx drops a connection idle for five seconds, and the
  arm's connect takes ten, so the connection would be gone before the first call.
- **Waiting for the warm-up.** It would move the same seconds from the first call to before it,
  and a probe that hung would hang the run.
- **More effort levels.** A level only one vendor has would be a flag one vendor's server refuses
  at the first turn, after the robot connected.

## Consequences

- `summary.json` gains five keys and the stepper's block three, and `run_start` gains `effort`
  and, for a pilot with a warm-up, `pilot_warm`. A shadow turn's `decision` line follows its
  `llm` line now, and its `decision_frame` line its `llm_request`.
- None of the seconds this saves is measured. The owed check is Step 0 of the plan: twenty
  timed requests per keyed decision LLM, and one `quackd run hello-world --robot
  microduck:mujoco --no-memory` per vendor, compared on per-call `latency_s` and time to the
  first `verb_start` against a run on this release.
- Shadow costs a turn only what is left of the stepper's second once the model has answered,
  so a shadow run's timings are close to a run without a stepper, which is what a measurement
  of the stepper wants.
