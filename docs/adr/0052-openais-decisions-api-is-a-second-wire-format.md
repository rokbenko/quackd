# ADR-0052: OpenAI's Decisions API is a second wire format, built in

**Status:** accepted · **Date:** 2026-10-10 · Amends [ADR-0043](0043-decision-llms-are-a-wire-format-and-a-data-row.md) (decision LLMs speak two wire formats rather than one, two rows carry a published rate rather than Jev's alone, and the hosts a key may go to live beside the client that sends it rather than in a row) and extends [ADR-0040](0040-a-discrete-stepper-in-front-of-the-model.md) (a question the decision LLM declines ends the turn on a gate of its own, `refused`, read before every other, and a number the gates read is a probability from 0 to 1 or the turn is `unreadable`) · Implemented in `quackd/agent/decision/decisions_api.py`, with changes in the catalogue, `base.py`, the factory, the stepper, the CLI and `quackd log` · Documented in [docs/guides/decision-llms/openai.md](../guides/decision-llms/openai.md)

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
  not a JSON object is an `UnreadableReply`, a `ValueError` whose sentence is quackd's own and
  names only the type that came back, so the record keeps it, calls the turn an error, and the
  model takes it.
- **The address is handed over on every build, and is the host alone.** The client is given
  `<url>/v1` whatever was typed, because left to itself the SDK follows `OPENAI_BASE_URL`, which
  quackd's own local presets read as the address of a model server. A `--decision-url` with a
  path, a query or a fragment is refused while the CLI reads its flags
  (`factory.resolve_decision_address`), so a copied `https://api.openai.com/v1` stops the run on
  every machine rather than only on one with the SDK and the key.
- **The key goes to OpenAI's own API and nowhere else.** `OPENAI_API_KEY` is sent only to
  `https://api.openai.com` or one of the regional hosts OpenAI's data controls page lists for
  `/v1/decisions`, `us.`, `eu.`, `au.`, `ca.`, `jp.`, `in.`, `sg.`, `kr.`, `gb.` and `ae.` in front
  of `api.openai.com`: the SDK's own regional choice names four of them, and a project with data
  residency in any of the others is told to add its region's prefix to every request. The rule
  reads the address's authority whole, which has to be one of those hosts, alone or with `:443`,
  over https, rather than `urlsplit`'s reading of the host, because the authority is what the
  client is handed, and a user in the address, or brackets on a Python older than 3.11.4, are
  where `urlsplit` and the HTTP client disagree about the host. The hosts live in
  `decisions_api.py` rather than in the row, whose address is only a default that
  `--decision-url` replaces. Every other address is sent `NO_KEY`, which moves to `base.py`, and
  is costed at the self-hosted rate unless `QUACKD_DECISION_PRICE` says otherwise, the key and the
  price decided by one rule on one string (`factory.sends_key`, which decides `jev`'s too).
  `QUACKD_DECISION_URL` is never read for this row.
- **What the SDK would add by itself is answered, in every release the extra allows.** The
  releases between the floor and the lock treat `OPENAI_CUSTOM_HEADERS` four ways: not read at
  all, every line added and merged by exact spelling, the authorization lines dropped when the
  caller names one, and every name merged without regard to case. So the rule rests on none of
  them. An authorization line of the variable, in any spelling, carries quackd's own value under
  that spelling, with `Authorization` itself omitted first when there is one, so whichever way a
  release merges, one line goes and it is quackd's. Every other name the variable carries is
  omitted, read the way the SDK reads it. The built client's headers are read back, and a release
  that would still send an authorization quackd did not write refuses the build, naming itself.
  `OpenAI-Organization` and `OpenAI-Project` go to OpenAI as the SDK fills them, a line of the
  variable that names either in the SDK's own spelling carrying the variable's value rather than
  taking the SDK's entry away, and are omitted at any address that is not OpenAI's. Omitting every
  name the variable carries, the obvious rule, breaks 3.22.1, which merges without regard to case:
  an omitted `authorization` takes quackd's own away with it.
- **No redirect is followed.** The client is built on the SDK's `DefaultAsyncHttpxClient`, told
  to follow none, because a redirect would carry the account headers, and on a 307 or a 308 the
  request with the state in it, to wherever it pointed, and 1.66.0 has no per-request way to say
  so. A redirect is a turn the record calls an error.
- **The stepper's second is the one clock that ends a turn, and no retries.** `max_retries=0`,
  because the SDK's two retries would sleep three eighths to a half of a second before the first.
  The client's own timeout is twice `TIMEOUT_S` (`base.client_timeout`) rather than `TIMEOUT_S`
  itself. httpx2 applies it to each operation of a request, the connect, each read, each write,
  rather than to the whole, and at the stepper's own second it came due with the stepper's bound,
  so one slow server's turns were recorded some as quackd's `no answer within 1.0 s` and some as
  `APITimeoutError`. Above it, the stepper's bound, an `asyncio.timeout` around `decide()`, ends
  every turn that runs out, and the record says `no answer within 1.0 s` only where that bound is
  what expired: a `TimeoutError` a backend raises on a clock of its own is recorded by its type.
  TypeSafe's client keeps its own clock above the second as well, so this holds on every row.
  `decide(timeout_s=...)` bounds the whole request with a `wait_for` of its own, the SDK's clock for
  that request set above the bound, for a caller with no stepper around it.
- **Nothing the SDK does once happens inside a turn.** Before a client's first request each release
  read, the eleven from 1.66.0 to 3.22.1 in the uv cache, asks which platform it is on, in a worker
  thread, and on Windows that is a WMI query or, on Python 3.11, a subprocess: inside the first turn
  it once took the whole second, before anything was sent. The client has the SDK's own function
  asked while it is built and is handed the answer (`warm_platform`), so no turn asks.
- **Translated both ways, and read by name alone.** The state goes as one JSON string, the very
  one the stepper measures as `state_chars`, with its letters as letters, because `input` is text
  the API hands its model undecoded and JSON's escapes would reach it as written. Every question
  goes named and in order: a `noul` as a `predicate`, a choice's criteria as `choices` with
  `escalate` last, a score's as `levels`. An answer is kept only under a name quackd asked, and
  position is never used, because the schema makes the name optional and position is the one reading
  that could attribute `need_human`'s probability to `done`. Every answer under a name is gathered
  before any is read: a refusal among them declines the question wherever it came, two answers or
  more answer nothing, and one answer counts only if it is the type its question went out as and
  carries that type's field, a string `choice` on a choice. First-wins, the rule this replaced, kept
  an answer ahead of a refusal, settled a contradiction in whichever direction came first, and let a
  `score` under `next_verb` or a choice with no `choice` read as a confident `escalate`. OpenAI's
  `confidence` is carried as the confidence, field to field, as Jev's is.
- **A declined question is a gate of its own.** Any name in the answer's `refused` ends the turn
  on `refused`, read before every other gate and whichever question it was, `feasible` included,
  with the names on the record and the summary counting `refusals`. Read as anything else it
  would be misnamed: a declined `done` would end on `unreadable`, and a declined `feasible` would
  not be noticed at all. `base.DecisionLLM` documents `refused` for any backend.
- **What a gate reads is a probability or nothing, on every row.** A server of your own that speaks
  this format is answered through an SDK that hands its JSON back unchecked, and the stepper read
  any number it could make a float of: a confidence written as text, or one on a scale of its own, a
  percentage or a 5, cleared its floor, a `done` or a `need_human` of -1 cleared its gate, and each
  moved the body. A confidence, `done` and `need_human` are now each a number from 0 to 1 and
  nothing else (`stepper._probability`), a `next_verb` without a string `choice` is no answer, and
  either ends the turn on `unreadable`. A NaN or an infinity never reaches the record, so every line
  of `transcript.jsonl` is JSON a strict reader takes.
- **A failed turn keeps its exception's type and HTTP status, and never its message.** OpenAI's
  SDK puts a 4xx body in the message and TypeSafe's the reply's own, and a reply can quote the
  request, which the record otherwise keeps as a size and a shape. This holds for every row. Each
  turn that hands its questions over also records `idle_s`, the seconds since the stepper's
  previous request ended, because both SDKs drop a connection idle for five seconds and a shadow
  turn usually waits longer than that between two of the stepper's. It is null on the first turn
  and on the one after a request that failed, which may have taken its connection with it, so it
  never claims a connection was kept warm that may be gone.
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
  object, and the wire tests have run against 3.22.1, which the lock carries, against 3.1.0 and
  against 1.66.0, the extra's floor, and against no other release.
- **Nothing here has answered a real robot, and no request from quackd has reached OpenAI.** In CI
  the client runs against a fake of the SDK. Wherever the SDK is installed, a real `quackd run` goes
  through it to a stand-in on 127.0.0.1 (`tests/fake_openai_decisions.py`), which shows on the wire
  where the key and the account headers go, and any test without the `live_decision` or `live_llm`
  marker that sends a request through the real SDK to a host under openai.com is stopped before it
  is sent, and fails, and a process such a test starts inherits an empty `OPENAI_API_KEY`. The live
  tests wait for a key.
