# Decisions API (OpenAI)

OpenAI's Decisions API, hosted: gpt-6-luna over POST /v1/decisions. It is made by OpenAI
([platform.openai.com](https://platform.openai.com/)), in public beta since 2026-10-06, and the
only interface is an API, so what quackd installs is not the model but its client: the `openai`
SDK the pilots already use, `quackd[openai]`, which the lock carries at **3.22.1** over a floor of
1.66. OpenAI's Decisions guide, its API reference and openai-python were read on 2026-10-08,
against the model id `gpt-6-luna`, and openai-python's 1.66.0 and 3.22.1 on 2026-10-09. It is the
one row on the table whose wire format is not System One's, so it is the one with a client that
translates, [`decisions_api.py`](../../../quackd/agent/decision/decisions_api.py), which turns
quackd's questions into OpenAI's shape on the way out and the answers back into quackd's. Every
name quackd spells for it otherwise lives in its row in
[`catalogue.py`](../../../quackd/agent/decision/catalogue.py).

**Nothing here has ever answered a real robot.**

```bash
uv pip install "quackd[openai]"    # not run here: the pilot's own extra, in quackd[all] too
export OPENAI_API_KEY=...          # not run here: the same key the openai pilot reads
quackd run arm-grip-check --robot lerobot:mock --llm fake --decision-llm openai --decision-mode shadow --no-memory    # not run here: no key on this machine
```

## The row

| Field | Value |
|---|---|
| `name` | `openai`, the same word `--llm` takes for OpenAI's chat models. The flag says which seam: `--llm openai` is the pilot, `--decision-llm openai` is this |
| `backend` | `decisions`, `POST /v1/decisions` through `decisions_api.py`, rather than the shared System One client |
| `summary` | `OpenAI's Decisions API, hosted: gpt-6-luna over POST /v1/decisions` |
| `install` | `quackd[openai] and OPENAI_API_KEY (platform.openai.com)` |
| `url` | `https://api.openai.com`, an address rather than `none`, because the client hands the SDK `<url>/v1` on every build instead of letting it find one |
| `key_env` | `OPENAI_API_KEY`, sent to OpenAI's own API and nowhere else |
| `model` | `gpt-6-luna`, the only model the endpoint serves |
| `model_env` | `none`: the SDK never chooses the address here, so no variable of its own chooses the model either |
| `price` | `Price(input=0.10, output=0.0, source="published", checked="2026-10-08")` |
| `extra` | `openai`, so `quackd[openai]`, the pilot's own |
| `sdk` | `openai` |
| `sees` | `False`, like every row: quackd sends it text and nothing else, although the API would take an image |

That is `PRESETS["openai"]` in `catalogue.py`. A test reads the address, the model id, the key
variable, the install line, the extra and the rate back off this page and compares them with the
row, so a cell that drifts fails the suite. `--decision-llm openai:<id>` names another model id for
one run, and `--decision-url` another address, under the rules below.

## What it is

A model OpenAI host that answers typed questions about a piece of text with probabilities rather
than prose: the same kind of thing as every other row on the table, in a different shape on the
wire. A request carries the state and every question at once; a yes or no question is a
`predicate`, a choice lists its options as values, and the answers come back as a list in which
each one names the question it answers. So the four questions quackd asks every decision LLM go out
translated, and the answers come back translated into the shape the stepper reads, by name and
never by position. It takes images as well as text, and quackd sends it text alone, the same JSON
state every other row is sent.

OpenAI charge **$0.10 per million input tokens, and nothing for output, cache reads or cache
writes**, which is the only figure of theirs used here. For speed they publish a ratio and no
latency: *"about 10x faster than the Responses API"*, which nothing here repeats as a quackd
measurement.

## What to know before you point a robot at it

- **Shadow first, here more than anywhere.** OpenAI do not define `confidence`. Their guide says
  a choice comes back with *"a probability distribution and a separate `confidence` field"* and
  stops there, their own examples put it below the top probability (0.93 against 0.95), and they
  claim no calibration: the advice is to set thresholds from labelled examples of your own. quackd
  carries their `confidence` as the confidence, field to field, the way it carries Jev's, and the
  four floors it is compared against were shaped around Jev. This row inherits all four
  unmeasured.
- **The address is the host alone.** quackd adds `/v1/decisions` itself, so a `--decision-url`
  with a path, a query or a fragment stops the run before anything connects, on any machine, with
  or without the extra and the key:

  ```
  $ quackd run arm-grip-check --robot lerobot:mock --llm fake --decision-llm openai --decision-url https://api.openai.com/v1 --no-memory
  ✗ error: --decision-url https://api.openai.com/v1 has a path: give the address alone,
  https://api.openai.com, because quackd adds /v1/decisions itself
  ```

- **The key goes to OpenAI's own API alone**: `https://api.openai.com`, or one of the regional
  hosts OpenAI's [data controls page](https://developers.openai.com/api/docs/guides/your-data)
  lists for this endpoint, `us.api.openai.com`, `eu.api.openai.com`, `au.api.openai.com`,
  `ca.api.openai.com`, `jp.api.openai.com`, `in.api.openai.com`, `sg.api.openai.com`,
  `kr.api.openai.com`, `gb.api.openai.com` and `ae.api.openai.com`, over https. The address's
  authority has to be one of those hosts and nothing more, or the host and `:443`, so a user, a
  port of somebody's own or a host that only looks like one of them is somebody else's. Every
  other `--decision-url` is a server of your own that answers the same format, and is sent the
  word `local`, as every server you run is. `QUACKD_DECISION_URL` is never read for this row,
  because it is a row that sends a key.
- **What the SDK reads for itself is decided here instead.** Handed no address it would follow
  `OPENAI_BASE_URL`, which quackd's own local presets read as the address of a model server on
  your own machine, so it is always handed one. It adds the lines of `OPENAI_CUSTOM_HEADERS` to
  every request, which is where a pilot that goes through a gateway keeps that gateway's headers,
  so none of their values goes: an authorization line, in whatever spelling, carries quackd's own
  key or word instead, a line naming an account header leaves that header to the SDK at OpenAI,
  and every other line is left off. `OPENAI_ORG_ID` and `OPENAI_PROJECT_ID` are OpenAI's
  identifiers, so they go to OpenAI as the SDK sends them, whatever a gateway's line says, and to
  nowhere else. That holds for every release of the SDK the extra allows, and a release that
  would still send an authorization line of the variable's stops the run and is named.
- **It follows no redirect.** Followed, one would carry the account headers, and on a 307 or a 308
  the whole request with the robot's state in it, to wherever it pointed. A redirect is a turn the
  record calls an error.
- **One second and no retries.** The SDK's own defaults are ten minutes and two retries, and its
  first retry sleeps three eighths to a half of a second before it starts. The client is built with
  no retries and a clock of its own at twice the stepper's, so the stepper's one-second bound is the
  clock that ends a turn, a turn that runs out of it is recorded as the stepper's
  `TimeoutError: no answer within 1.0 s` rather than, as it once was at random, as the SDK's own
  timeout, and a 429 or a 5xx is a turn the record calls an error. Before a client's first request
  the SDK also asks which platform it is on, which on a cold Windows machine is a WMI query or a
  subprocess, so quackd has that asked while the client is built, before the robot connects, and no
  turn waits for it.
- **A question it declines ends the turn on a gate of its own.** The API may refuse any one
  question rather than answer it, and the stepper reads that before any answer: the turn goes to
  the model on the gate `refused`, whichever of the four was declined, `feasible` included, with
  the names on the turn's record and the run's summary counting such turns as `refusals`. A
  refusal is never read as an answer that went missing or as a low probability.

> [!WARNING]
> **`--decision-url` on this row changes what is sent and what it costs as well as where it goes.**
> An address that is not OpenAI's own is a server you run, so it is sent the word `local` instead
> of `OPENAI_API_KEY`, and its turns are costed at the self-hosted $0 rather than at OpenAI's rate.
> `QUACKD_DECISION_PRICE` is how you say otherwise, for a paid server or for OpenAI's regional
> premium, which OpenAI say applies and publish for no Decisions row.

## VERIFIED (read from OpenAI's Decisions guide, its API reference and openai-python on 2026-10-08, from openai-python 1.66.0 and 3.22.1 on 2026-10-09, and from OpenAI's data controls page and the openai-python releases between 1.66.0 and 3.22.1 that uv had cached on 2026-10-10)

| Thing | Value | What quackd does with it |
|---|---|---|
| The endpoint | `POST /v1/decisions`, in public beta since 2026-10-06 with general availability expected *"in the coming weeks"*, and `gpt-6-luna` *"the only model currently available"* | the row's address and model id; the client posts to `/decisions` under `<url>/v1` |
| The request | exactly `model`, `input`, `questions` and an optional `safety_identifier`; `input` is a string or a list of user messages | `model`, `input` and `questions` and nothing else. `input` is the state as one JSON string, the very string the stepper measures as `state_chars`, with its letters as letters: the API never decodes `input`, so JSON's escapes would reach the model as written, a `ž` as `\u017e` |
| Questions | `predicate` (`instructions`, `name`), `choice` (`choices: [{value, description}]`, values typed, so the string `"true"` and the boolean `true` are different values) and `score` (`levels: [{label, description}]`, whose indices start at 0). The name is optional, and the guide says to give every question a unique one, which the API echoes | every question is named; a `noul` goes as a `predicate`, a choice's criteria as `choices` with every label a string and `escalate` last, and a score's as `levels` in the order they were written |
| Answers | `{answers, model, usage}`, the answers in question order, each naming its question or `null`: a `predicate` carries `probability`, a `choice` `choice`, `confidence` and `probabilities: [{value, probability}]`, a `score` `score`, `confidence` and `probabilities: [{value, label, probability}]` | read by name and never by position: an answer under a name quackd did not ask, or under none, is dropped. Every answer under a name is gathered first: a refusal among them declines the question, two answers or more answer nothing, and one answer counts only if it is the type its question went out as with that type's field, a string `choice` on a choice. A `probability` becomes the `noul` the stepper reads, and a list of probabilities a mapping, NaN and the infinities left out. Every number is then judged as every row's is: a confidence or a Noul a gate reads that is not a number from 0 to 1 ends the turn on `unreadable` |
| Refusals | `{type: "refusal", name}` in place of one question's answer: *"The host may decline one question without disclosing its refusal score"* | the question is left unanswered, and its name is handed back beside the answers as `refused`, which ends the turn on the stepper's gate of that name and hands it to the model |
| `confidence` | not defined; the guide's examples have it below the top probability | carried as the stepper's confidence, field to field, and gated on the floors like any other row's, with the distribution recorded beside it |
| Usage | `input_tokens`, `input_tokens_details` (`cached_tokens`, `cache_write_tokens`), `output_tokens`, `output_tokens_details` (`reasoning_tokens`), `total_tokens` | passed through, so a turn is billed on the `input_tokens` OpenAI counted rather than on an estimate |
| Price | $0.10 per million input tokens, no output, cache-read or cache-write charges, and *"regional processing premiums and long-context input pricing multipliers apply"*, neither published for this endpoint ([the guide](https://developers.openai.com/api/docs/guides/decisions)) | `Price(input=0.10, output=0.0)`, dated 2026-10-08 on the record; a regional host is costed at this rate too, and `QUACKD_DECISION_PRICE` is how to say otherwise |
| Data | not used for training, kept 30 days in abuse-monitoring logs, eligible for zero data retention | nothing to do; [SECURITY.md](../../../SECURITY.md) says what a run sends |
| The SDK's resource | `client.decisions` arrived in openai 3.26.0, on 2026-10-06; the lock carries 3.22.1, which has none | not used. The client makes the SDK's generic request, `post(path, cast_to=object, body=..., options=...)`, which hands back the decoded JSON body unchanged in 1.66.0 and in 3.22.1, so no floor moves |
| The SDK's address | given none, it reads `OPENAI_BASE_URL`, else `https://api.openai.com/v1`, and it appends a request's path after making sure the address ends in a slash. Its own regional choice, in 3.22.1, names four hosts: `api`, `us.api`, `eu.api` and `ae.api.openai.com` | handed `<url>/v1` on every build, built from the scheme and the authority the key was decided on |
| Regional hosts | the data controls page lists ten regions, each with a host, `us`, `eu`, `au`, `ca`, `jp`, `in`, `sg`, `kr`, `gb` and `ae` in front of `.api.openai.com`, and `/v1/decisions` among the endpoints each stores in its region; it says *"The Decisions API is available in all supported API regions"*, that a project with data residency adds its region's prefix to every request, and that *"Availability in a region does not imply that inference is performed in that region"*, while the Decisions guide says *"Data residency and regional processing are supported in the United States and Europe (EEA + Switzerland)"* | the key goes to `api.openai.com` and to each of those ten, and to no host OpenAI have not named, however much it looks like theirs (`decisions_api.OPENAI_HOSTS`) |
| The SDK's headers | it adds `OpenAI-Organization` and `OpenAI-Project` from `OPENAI_ORG_ID` and `OPENAI_PROJECT_ID`, and an `Omit` in place of a value drops a header. What it does with `OPENAI_CUSTOM_HEADERS` depends on the release: 1.66.0 and 1.83.0 do not read it; 3.1.0 adds every line, split on newlines with the name before the first colon, and lets a header the caller names replace a line only when both are spelled alike; the releases read from 3.6.0 to 3.19.0 also drop the variable's authorization lines when the caller names one in any spelling; 3.22.1 also merges every name without regard to case | an authorization line of the variable, in any spelling, carries quackd's own value under that spelling, with `Authorization` itself omitted first when there is one; at OpenAI a line naming an account header in the SDK's own spelling carries the variable's value, and so does any other spelling of it beside that one; every other name is omitted, and so are both account headers at any address that is not OpenAI's. The built client's headers are read back, and an authorization value quackd did not write refuses the build, naming the release. Each of the eleven releases in that range that uv had cached was driven against a socket on 127.0.0.1 with the variable's authorization line spelled three ways, and sent one authorization line every time, quackd's |
| Redirects | the SDK follows them, because the HTTP client it builds for itself does; 1.66.0 has no per-request option to say otherwise, and a release that does not know an option drops it without a word | the client is built on `DefaultAsyncHttpxClient(follow_redirects=False)`, which every release reads, so a 3xx is an error and nothing follows it |
| The SDK's clocks | 600 s a request (5 s to connect) and two retries, the first after three eighths to a half of a second, or as long as a `Retry-After` asks up to two minutes. httpx2, which it sends through, applies a timeout to each operation of a request, the connect, each read, each write and the wait for a pooled connection, rather than to the whole (2.12.0) | `max_retries=0` and a timeout at twice `TIMEOUT_S`, so `asyncio.timeout` around `decide()` in the stepper is the one clock that ends a turn. `decide(timeout_s=...)`, for a caller with no stepper around it, bounds the whole request with a `wait_for` of its own and sets the SDK's clock for that request above it |
| The SDK's first request | every release from 1.66.0 to 3.22.1 that uv had cached starts a client's first request by asking which platform it is on, `platform.system()` and `platform.platform()`, in a worker thread; on Windows `platform.platform()` asks the system for its version, by a WMI query from Python 3.12 and by starting `cmd /c ver` on 3.11, and CPython keeps the answer for the life of the process | asked while the client is built, before the robot connects, with the SDK's own function, and the answer handed to the client, so no stepper turn spends its second on it |

## UNVERIFIED, and what quackd does about each

| Name | The assumption | What quackd does |
|---|---|---|
| `CONFIDENCE_MEANS_THE_SAME` | that OpenAI's `confidence` is comparable to the one the floors were set against. Nothing published says what it is, and nothing here has measured it | nothing automatic; `--decision-mode shadow` records what it would have chosen without letting it act, which is how a floor gets re-tuned |
| `CALIBRATED` | that a probability of 0.9 from it is right about nine times in ten. OpenAI claim no calibration and say to set thresholds from labelled examples of your own. Two testers on [OpenAI's forum](https://community.openai.com/t/1403877) have reported on it, on questions of their own rather than a robot's: one found that, over a coin weighted seventy to thirty, a predicate came out heads 70% of the time where a choice came out heads 98% of the time, and that the order of a choice's options moved its probabilities; the other found it slightly less well calibrated than Jev, with about three times as many confident wrong answers | nothing automatic. `next_verb` is a choice, so those are the reports that say to draw a calibration curve from your own shadow runs before trusting `on`, and a floor moves on one ([How a turn is decided](README.md#how-a-turn-is-decided)) |
| `LATENCY_UNDER_ONE_SECOND` | that it answers a robot's state inside the turn budget. OpenAI publish a ratio and no latency, and no request from quackd has ever timed it. Two figures posted on the same forum, neither of a robot's state, are about 0.8 s for a request carrying an image over a 3 Mbps link, from a moderator there, and one tester's median turn at 1.6 s against Jev's 0.3 s in their own multi-step use | `TIMEOUT_S` is 1.0 s, enforced around `decide()`; a turn that runs out goes to the model and the record says how long it waited. The SDK drops a connection that has sat idle five seconds, and in shadow mode the model's own call and a verb usually take longer than that between two of the stepper's requests, so each turn also records `idle_s`, the seconds since the stepper's previous request ended, which tells a turn that paid for opening a new connection from one that reused it. It is null on the first turn and on the one after a request that failed, which may have taken its connection with it, so read those apart as well |
| `CHOICE_BOUNDS` | the number of options a choice may carry. One reading of the reference says 2 to 255, and it could not be found again in any source | nothing; quackd always offers at least two (a call and `escalate`), and a request the API refuses is a turn the record calls an error |
| `ANSWER_NAMES_ECHOED` | that every answer carries the name it was asked under, as the guide says | nothing to rely on: answers are read by name alone, so one without its name is a question nobody answered, and the turn goes to the model |
| `IMAGE_TOKENS` | how a picture is counted and billed. The API takes one, and `gpt-6-luna` is in none of the vision guide's sizing or multiplier tables, so what a camera frame would cost on this endpoint is not published | nothing, because quackd sends this row text alone and its `sees` is false. A turn is billed on the `input_tokens` OpenAI report rather than on a formula of quackd's, so whatever they count is what the record says |
| `NO_CACHE_DISCOUNT` | how cached input is billed. The guide charges nothing for cache reads or writes, and `usage` still reports `cached_tokens` | every input token is costed at the full rate, which is the higher reading |
| `REGIONAL_RATE` | the premium on the regional hosts and the long-context multiplier, which apply and are published for no Decisions row | the published rate everywhere OpenAI is reached; `QUACKD_DECISION_PRICE` for a rate of your own |
| `MODEL_ALIAS_MOVES` | whether `gpt-6-luna` names one fixed model or moves under a run | nothing; the record names the id quackd asked for |
| `GENERIC_POST_ACROSS_VERSIONS` | that the generic request keeps handing back the decoded body unchanged in every SDK between the floor and the cap. It was read in 1.66.0 and 3.22.1 | a body that is not a JSON object is a turn the record calls an error, in quackd's own words rather than the body's, `UnreadableReply: POST /v1/decisions answered str rather than a JSON object` for a page of text, and the model takes it |
| `BETA_SHAPE` | that the shapes above survive the beta | the reader is strict by name and by type, and leaves unanswered an answer of a type it does not know, of a type its question was not asked as, or without its type's field, so a renamed field costs a turn on `unreadable`, where a counter of gates sees it, and never the run |

## Status

It has never answered a real robot, and this page says so until somebody reports that it has. In
the suite the client is exercised against a fake of the SDK
([`tests/fake_decisions_api.py`](../../../tests/fake_decisions_api.py)) rather than against OpenAI,
and wherever the SDK is installed the real one drives a real `quackd run` against a stand-in for the
API on 127.0.0.1 ([`tests/fake_openai_decisions.py`](../../../tests/fake_openai_decisions.py),
[`tests/test_decisions_api_wire.py`](../../../tests/test_decisions_api_wire.py)), which shows the
headers the SDK actually sends where the key is meant to go and where it is not. Both prove quackd's
half of the conversation and not OpenAI's. No request from quackd has reached OpenAI: the tests that
would, [`tests/test_live_decision.py`](../../../tests/test_live_decision.py), wait for a key, and
any other test that sends a request through the SDK to a host under openai.com is stopped before it
is sent, and fails. Without the extra, `quackd doctor` prints this row as
`missing (quackd[openai])`, `OPENAI_API_KEY unset`, `gpt-6-luna` and `https://api.openai.com`, and a
run that names it says so once before it connects and carries on without the stepper:

```
$ quackd run arm-grip-check --robot lerobot:mock --llm fake --decision-llm openai --decision-mode shadow --no-memory
⚠ --decision-llm openai asked for, running without it: --decision-llm openai needs the optional extra quackd[openai] — run: uvx --from "quackd[openai]" quackd ...  or: uv pip install "quackd[openai]"
```

`--decision-mode shadow` is how that status changes.

## How to help

Run it in shadow mode on your own bench and report what came back. The commands and the rows to
read are in [Measuring it yourself](README.md#measuring-it-yourself), and what is worth an issue
is the agreement rate, the real latency against a robot's state, a refusal and the state that drew
it, and whether `confidence` sits below the top probability the way OpenAI's examples have it. Say
which host you reached, `api` or a regional one, because the rate and the latency can both differ
between them. A floor moves on calibration rather than opinion, confidence bucketed against
agreement per verb class, as [How a turn is decided](README.md#how-a-turn-is-decided) sets out.
