# ADR-0052: OpenAI's Decisions API is a second wire format, built in

**Status:** accepted · **Date:** 2026-10-10 · Amends [ADR-0043](0043-decision-llms-are-a-wire-format-and-a-data-row.md) (decision LLMs speak two wire formats rather than one, two rows carry a published rate rather than Jev's alone, and the hosts a key may go to live beside the client that sends it rather than in a row) and extends [ADR-0040](0040-a-discrete-stepper-in-front-of-the-model.md) (a question the decision LLM declines ends the turn on a gate of its own, `refused`, read before every other) · Implemented in `quackd/agent/decision/decisions_api.py`, with changes in the catalogue, `base.py`, the factory, the stepper, the CLI and `quackd log` · Documented in [docs/guides/decision-llms/openai.md](../guides/decision-llms/openai.md)

## Context

OpenAI opened its Decisions API to every developer on 2026-10-06, in public beta, with general
availability expected "in the coming weeks" and `gpt-6-luna` its only model. `POST /v1/decisions`
takes text, or text and inline images, with a list of typed questions, and answers each with
probabilities rather than prose, at $0.10 per million input tokens and nothing for output. That
is the kind of question [ADR-0040](0040-a-discrete-stepper-in-front-of-the-model.md)'s stepper
asks, four at a time: a choice among the calls on offer with a way out, two yes-or-no questions,
and a feasibility choice it only records.

It does not speak System One. [ADR-0043](0043-decision-llms-are-a-wire-format-and-a-data-row.md)
made the wire format the abstraction, and every server row since has been the same
`POST /v1/systemone` behind one client. OpenAI's shape differs at every point quackd reads:

- the questions go as a list rather than a mapping keyed by name, and the name on each is
  optional;
- a yes-or-no question is a `predicate` rather than a `noul`, and a choice's options are a list of
  `{value, description}` rather than a mapping of label to description;
- the answers come back as a list in question order, each naming its question or `null`, and a
  choice's probabilities are a list of `{value, probability}` where quackd's reader expects a
  mapping;
- any one question may come back as `{type: "refusal", name}` in place of an answer.

Handed to the stepper as it comes, that answer would have read as no answer on every turn.
ADR-0043 had already met an endpoint it could not reach by moving an address, OpenRouter's
`/api/alpha/decisions`, and the decision-LLM hub said a row for that would be a second transport
rather than a second address. This is that case, from the vendor itself.

The lock carries openai 3.22.1. The SDK's own `decisions` resource arrived in 3.26.0, on the day
the API opened, and the eleven extras that install the SDK, `all` among them, pin it at
`>=1.66,<4`.

## Decision

- **A backend of its own, beside `systemone.py`.** `DECISIONS_API = "decisions"` in the
  catalogue, `quackd/agent/decision/decisions_api.py` with its `DecisionsAPILLM`, and a branch in
  `make_decision_llm`. It is named for the format rather than the vendor, as `SYSTEM_ONE` is: a
  server of your own that answers `/v1/decisions` is the same backend at another address. The row
  is `openai`, second after `jev`: `https://api.openai.com`, `gpt-6-luna`, `OPENAI_API_KEY`, the
  pilot's own `openai` extra and SDK, `Price(0.10, 0.0)` published and checked on 2026-10-08, no
  model variable, and `sees` false.
- **The SDK's generic request, so no floor moves.** `client.post("/decisions", cast_to=object,
  body=...)` hands back the decoded JSON body unchanged in 1.66.0 and in 3.22.1. A body that is
  not a JSON object is a `ValueError`, which the record calls an error and the model takes.
- **The address is handed over on every build, and is the host alone.** The client is given
  `<url>/v1` whatever was typed, because left to itself the SDK follows `OPENAI_BASE_URL`, which
  quackd's own local presets read as the address of a model server. A `--decision-url` with a
  path, a query or a fragment is refused while the CLI reads its flags
  (`factory.resolve_decision_address`), so a copied `https://api.openai.com/v1` stops the run on
  every machine rather than only on one with the SDK and the key.
- **The key goes to OpenAI's own API and nowhere else.** `OPENAI_API_KEY` is sent only to
  `https://api.openai.com` or its `us.`, `eu.` and `ae.` hosts, the four the SDK itself names, on
  port 443 or none and with no user in the address, because a user in it is where `urlsplit` and
  the HTTP client can disagree about the host. The four live in `decisions_api.py` rather than in
  the row, whose address is only a default that `--decision-url` replaces. Every other address is
  sent `NO_KEY`, which moves to `base.py`, and is costed at the self-hosted rate unless
  `QUACKD_DECISION_PRICE` says otherwise. `QUACKD_DECISION_URL` is never read for this row.
  `Authorization` is named outright, every header `OPENAI_CUSTOM_HEADERS` names is omitted, read
  the way the SDK reads it, and `OpenAI-Organization` and `OpenAI-Project` are omitted at any
  address that is not OpenAI's.
- **One second and no retries.** `timeout=TIMEOUT_S` and `max_retries=0`. The SDK's two retries
  would sleep three eighths to a half of a second before the first, and the stepper's `wait_for`
  stays the bound that holds.
- **Translated both ways, and read by name alone.** The state goes as one JSON string, the very
  one the stepper measures as `state_chars`. Every question goes named and in order: a `noul` as a
  `predicate`, a choice's criteria as `choices` with `escalate` last, a score's as `levels`. An
  answer is kept only under a name quackd asked, the first under a name wins, and position is
  never used, because the schema makes the name optional and position is the one reading that
  could attribute `need_human`'s probability to `done`. OpenAI's `confidence` is carried as the
  confidence, field to field, as Jev's is.
- **A declined question is a gate of its own.** Any name in the answer's `refused` ends the turn
  on `refused`, read before every other gate and whichever question it was, `feasible` included,
  with the names on the record and the summary counting `refusals`. Read as anything else it
  would be misnamed: a declined `done` would end on `unreadable`, and a declined `feasible` would
  not be noticed at all. `base.DecisionLLM` documents `refused` for any backend.
- **A failed turn keeps its exception's type and HTTP status, and never its message.** OpenAI's
  SDK puts a 4xx body in the message and TypeSafe's the reply's own, and a reply can quote the
  request, which the record otherwise keeps as a size and a shape. This holds for every row. Each
  turn that sends a request also records `idle_s`, the seconds since the stepper's previous
  request ended, because both SDKs drop a connection idle for five seconds and a shadow turn
  usually waits longer than that between two of the stepper's.
- **The floors do not move.** OpenAI define no `confidence` and claim no calibration, so this row
  inherits all four floors unmeasured, like every other, and shadow mode comes first.
- **One word, two seams.** `--llm openai` is OpenAI's chat models flying the robot and
  `--decision-llm openai` is this, in front of it. The flag says which, and the pilot's key and
  the pilot's extra serve both.

## Why not

- **A plugin.** The entry point group is how a decision LLM quackd does not name gets in, and it
  would have worked. But a plugin's row carries no price, so a paid API would be costed at the
  self-hosted $0, and [ADR-0041](0041-the-record-says-when-it-ran-and-what-it-cost.md) already
  said why that is wrong: a missing rate and a free model are different facts. Nor is a plugin
  held to the catalogue's tests or to a page a test reads back.
- **`client.decisions`, with the floor raised to 3.26.** It is the SDK's own resource and the
  obvious call. It would also raise the floor in all eleven extras and make everybody who pilots
  through an OpenAI-shaped vendor upgrade for a stepper they may never name, for a request the
  generic call already makes.
- **The standard library.** urllib would lose the pooled connection between turns, the typed
  errors whose class names the record keeps, and the real-SDK wire test that
  [ADR-0050](0050-a-router-is-a-vendor-whose-list-is-read-on-the-day.md)'s OpenRouter work set the
  pattern for.
- **OpenRouter's `/api/alpha/decisions`.** It serves `openai/gpt-6-luna-decisions` and takes
  System One's questions, but at a path of its own that the System One client cannot reach, and
  behind a third vendor's key. OpenAI's own endpoint is the shorter way to the same model.
- **Translating in `stepper.py`.** The stepper asks everybody the same four questions and reads
  everybody's answers the same way. A backend that speaks another shape translates on its own
  side, so the stepper never learns which one it was talking to.
- **The top probability, or a blend, in place of `confidence`.** OpenAI's own examples put
  `confidence` below the top probability, 0.93 against 0.95, and a number quackd made up from the
  distribution would be one nobody published. Both are recorded, so shadow mode can compare them.
- **One predicate per verb.** One tester on OpenAI's forum found a predicate following a weighted
  coin where a choice overshot it and moved with the order of its options. Asking that way would
  break the same four questions to every backend, and the floors that gate a choice's
  confidence, on one anecdote. Shadow data is what would justify it.
- **Retrying inside the second.** Within one second a 429 or a 5xx is worth more as a turn the
  record calls an error than as a retry that cannot finish, and a retry that honoured a
  `Retry-After` could sleep for two minutes.

## Consequences

- Two wire formats, two hosted rows, and two rows with a published rate. The catalogue tests
  loosen to say so: a row's backend is one of three, a key and a price belong to `jev` and
  `openai`, and the model variable to `jev` alone.
- A run that names `openai` sends the state's text to OpenAI, whose data controls say it is not
  used for training and is kept in abuse-monitoring logs for 30 days
  ([SECURITY.md](../../SECURITY.md)). Nothing else goes: `sees` is false on every row, and whether
  a camera frame should ever reach a decision LLM is a separate decision, which would need a flag
  of its own.
- The record grows the `refused` gate and its names, `refusals` in the summary, and `idle_s` on
  each turn that sent a request, and every row's `error` is now a type, with a status where there
  is one.
- The generic request rests on two of the SDK's internals: `cast_to=object` passing the body
  through, and a body that is not JSON coming back as text. The reader checks that it got a JSON
  object, and the wire test runs against the locked 3.22.1.
- **Nothing here has answered a real robot, and no request from quackd has reached OpenAI.** In
  CI the client runs against a fake of the SDK. Wherever the SDK is installed, a real
  `quackd run` goes through it to a stand-in on 127.0.0.1 (`tests/fake_openai_decisions.py`),
  which shows on the wire where the key and the account headers go, and any test without the
  `live_decision` marker that sends a decision request through the real SDK to OpenAI's own hosts
  is stopped before it sends, and fails. The live tests wait for a key.
