# ADR-0059: A turn the model's API could not answer is asked again

**Status:** accepted · **Date:** 2026-10-23 · Extends [ADR-0012](0012-safety-executor.md) (the kill switch, the heartbeat and the teardown every run ends with) · Implemented in `quackd/agent/providers/base.py` (`transient_failure`), `quackd/agent/loop.py` (`_ask_the_model`), and in `quackd/log.py`, `quackd/ui.py` and `quackd/cli.py` (`run_counters`) for what a person sees ([safety](../concepts/safety.md#when-the-models-api-fails))

**Amended 2026-10-23:** the two separate changes the consequences below name were made the same
day, at Rok's word.

- **The SDKs retry nothing of their own.** Every anthropic and openai client is built with
  `max_retries=0` (`NO_SDK_RETRIES`), the read-only client the openai provider builds to read the
  SDK's own headers included, and google-genai retries nothing by default. Every try is the
  loop's, on the record, under the stop and inside `max_minutes`, and a `retry-after` of minutes
  can no longer hold a call out of reach of a first Ctrl-C. The quick tries a first connection
  had from the SDKs are kept as the loop's own: a connection that failed or timed out before the
  API had answered once in the run is tried again after 0.5 s and then 1 s
  (`FIRST_CONNECTION_TRIES_S`), with no note, and only then ends the run as decision 3 says.
- **A first press ends a call in flight.** The pilot's call and a decision LLM's are raced
  against the abort, as a verb is (`_unless_stopped`), so the press wins at once and the answer,
  if it comes, is never read. A second Ctrl-C that lands inside the call is carried back to the
  run, so its teardown still runs.

Two more came with them. `x-should-retry: true` asks again at any status, as the SDKs read it,
where only `false` was read before. And a run that gave up prints its counters under the line it
ended on, as a run that ended any other way prints them under its panel.

**Amended 2026-10-26 by [ADR-0064](0064-a-run-says-where-its-seconds-went-and-waits-for-less.md):** each try is raced against the task's
minutes as well as the stop, so a call that hangs ends the run on its budget once
`max_minutes` is spent, its `llm` record saying so, rather than holding the robot for as
long as the vendor's own clock allows.

## Context

On 2026-10-23 Rok asked what a run does when its goal fails for a reason that is not the model
refusing the work: an error, or whatever else could go wrong. Rok wanted such a run to try again
where trying again could work, and to end with the arm at its rest pose where it could not.

The second half was already so: every way a run ends goes through one teardown that stops the
robot and folds the arm to its rest pose
([ADR-0058](0058-letting-go-of-the-arm-means-putting-it-down-first.md) has the release). Most of
the first half was too. A verb that raises, times out or is refused is handed back to the model
as a failed result, and the model chooses again; a serial packet the arm garbled is read again
by the bus before anything gives up (upstream's `num_read_retries`, and quackd's own `num_retry`
on the torque and temperature registers).

What ended a run at once was the model's API. A rate limit (429), Anthropic's 529 "overloaded",
a 5xx, a timeout or a dropped connection reached the loop as a `ProviderError` and ended the run
`error` after whatever retries the SDK made of its own: two for anthropic and openai, about half
a second and a second apart unless the API asked for longer, and none for gemini. An overloaded
minute outlasts them. On a bench that means the arm folds and a run set up with care ends for a
reason that had nothing to do with the task or the robot.

## Decision

1. **The SDK's own failure says whether to ask again.** Every provider that talks to an SDK
   raises its `ProviderError` `from` the SDK's exception: anthropic, gemini, and openai with the
   ten providers built on its SDK, openrouter and the local presets among them.
   `transient_failure` follows that `__cause__` chain to it. A status of 408, 429 or any 5xx,
   529 included, is worth asking again, unless the API says `x-should-retry: false`; with no
   status, a timeout or a lost connection is, by the class names in the MRO. Everything else is
   permanent and ends the run at once as before: a bad request, a key that does not work, a
   model that does not exist, a refused redirect, an answer quackd could not read as a turn, a
   missing extra. So is a request quackd built wrong, though the SDKs raise it as a lost
   connection: it is found beneath their `APIConnectionError` (`BUILT_WRONG_ERRORS`). The
   message is never read, so nothing quackd raised itself is ever asked again. An implicit
   `__context__` is not followed.

2. **The same turn is asked again, unchanged.** The history is built once per turn and resent
   byte for byte, so a model that binds its replayed thinking to what came before it sees the
   request it would have seen. The waits are `ASK_AGAIN_AFTER_S` = 5, 15, 30 and 60 s, so five
   tries in all and 110 s of waiting unless the API asks for longer. Each scheduled wait loses up
   to a quarter at random (`ASK_AGAIN_JITTER`), so a flock on one key spreads out, and no wait is
   ever shorter than a `retry-after` the API sent. A `retry-after` longer than
   `LONGEST_ASKED_WAIT_S` (120 s, the openai SDK's own ceiling in 3.22.1) ends the run instead,
   and so does a wait longer than what the task's `max_minutes` has left.

3. **A connection that never answered fails fast.** A timeout or a lost connection from a
   provider whose API has not answered once this run ends the run at once, because a connection
   that never answered is as often a wrong address or a server not started as a dropped one, and
   a person told two minutes later is told late. Once the API has answered, with a turn or with
   a status, the same failure is a drop, and asked again: a 529 and then a lost connection is an
   overload, not a wrong address. Statuses are asked again from the first call, since a server
   that answered with one is there.

4. **The robot holds still, and the run stays stoppable.** Nothing is sent to the robot while the
   run waits: it is the same idle state as a model taking a minute to think, and the heartbeat
   keeps watching the body as its own task. Ctrl-C, `q`, a heartbeat that fails and a flock that
   stops its members all set the run's abort flag, which ends the wait at once as `aborted`, and
   the teardown folds the arm as it does for every abort. A failure that arrives after the flag was
   set is not asked again. The second Ctrl-C still ends the run as an interrupt.

5. **A retry is not charged to `max_llm_calls`.** That budget is the task author's bound on how
   many turns the model takes, and a retry is the same turn asked again. Charging it would let
   one busy minute at a vendor spend a task's turns: `hello-world`, which fits its five calls
   exactly, would end `budget` after a single 429. The tries are bounded already, by the
   schedule's length and, on a real robot, by `max_minutes`. A simulator's clock, which
   `max_minutes` is read on there, stands still while the loop waits.

6. **Every try is on the record.** Each failed call keeps its `llm` record with the error and
   its latency, which still counts toward `llm_latency_s`, and gains `attempt` from the second
   try and `retry_in_s` where quackd will wait; each `llm_request` carries `attempt` from the
   second, and so does the `llm` record of a try that answered. The summary gains `llm_retries`
   and `llm_retry_wait_s` only when the run waited to ask again, a wait a stop ended counting
   its seconds and no try, so every older summary reads as it did, and the counters under a
   run's verdict say `(+N asked again)` and how long the waits took. A run out of tries, out of
   minutes, or asked to wait too long ends `error`, its reason saying which.

## Consequences

- A run on a busy API goes on where it used to end. The first wait of a run comes with a note
  saying the robot holds still, that the heartbeat goes on watching it and that Ctrl-C or `q`
  ends the run instead, and each try after has a log line saying when quackd asks again.
- A request that timed out or lost its connection may have been processed and billed by the
  vendor before quackd asks it again; `cost_usd` cannot see a bill that never came back. The SDKs'
  own retries have the same gap, and neither sends an idempotency key.
- The SDKs' own retries still come first, inside the call. The anthropic SDK honours a
  `retry-after` of any length and the openai SDK one up to 120 s, so a call can wait minutes
  before it fails, counted as the model's seconds rather than as quackd's waits and out of reach
  of a first Ctrl-C, which only the second ends. Building the SDK clients with `max_retries=0`,
  so that quackd's loop is the only one that retries, is a separate change, and it would also
  take away the quick retries a first connection has now.
- A server error that will not go away, a local server out of memory or a gateway with no
  provider it may route to, now takes the whole schedule, a minute and a half or more, to end
  the run.
- A first Ctrl-C still does not end a call already in flight: the abort is noticed when the call
  returns, as before. Racing the call against the abort, as a verb is raced, is a separate change.
- The decision LLM, the coordinator flock's planner and the browser demo are left as they were:
  the stepper already hands a turn it could not decide to the pilot (`quackd/agent/decision/
  stepper.py`), inside a bound of its own, and the planner falls back to its defaults.
- Nothing here has retried against a real 429 or 529: only against stand-ins of the SDKs'
  exceptions and against the real openai SDK talking to a stand-in server.

## Alternatives

- **Raise the SDKs' own `max_retries`.** One line per provider, and wrong in three ways: their
  sleeps do not hear a first Ctrl-C, do not know the task's `max_minutes`, and leave nothing on the
  record, so a run would sit silent for minutes and then say nothing about why.
- **Flags set by each provider's classifier.** Three edits that would all call this same logic,
  and a provider that forgot one would silently never ask again.
- **Read the message.** quackd's own refusals and every vendor's wording would then decide, and
  a test's `ProviderError("rate limited")` would mean something different from a real 429.
- **Follow `__context__` too.** Raised while handling a rate limit is not raised because of one.
- **Charge `max_llm_calls`**, or end `budget` when out of minutes: see decision 5; a run that ran
  out of minutes waiting on an API ends `error` with that reason, which says what happened.
