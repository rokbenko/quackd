# Decisions API (OpenAI)

OpenAI's Decisions API, hosted: gpt-6-luna over POST /v1/decisions. It is made by OpenAI
([platform.openai.com](https://platform.openai.com/)), in public beta since 2026-10-06, and the
only interface is an API, so what quackd installs is not the model but its client: the `openai`
SDK the pilots already use, `quackd[openai]`, which the lock carries at **3.22.1** over a floor of
1.66. OpenAI's guide and API reference were read on 2026-10-08, against the model id
`gpt-6-luna`, and the SDK on 2026-10-09. It is the one row on the table that does not speak System
One, so it is the one with a client of its own,
[`decisions_api.py`](../../../quackd/agent/decision/decisions_api.py), which translates quackd's
questions out and the answers back. Every name quackd spells for it otherwise lives in its row in
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

- **The key goes to OpenAI's own API alone**: `https://api.openai.com` or its regional hosts
  `us.api.openai.com`, `eu.api.openai.com` and `ae.api.openai.com`, over https, on port 443 or
  none, with no user named in the address. Every other `--decision-url` is a server of your own
  that answers the same format, and is sent the word `local`, as every server you run is.
  `QUACKD_DECISION_URL` is never read for this row, because it is a row that sends a key.
- **What the SDK reads for itself is decided here instead.** Handed no address it would follow
  `OPENAI_BASE_URL`, which a reader sets for a model on their own machine, so it is always handed
  one. It adds the lines of `OPENAI_CUSTOM_HEADERS` to every request, which is where a pilot that
  goes through a gateway keeps that gateway's headers, so every one is left off. `OPENAI_ORG_ID` and
  `OPENAI_PROJECT_ID` are OpenAI's identifiers, so they go to OpenAI as the SDK sends them and to
  nowhere else.
- **One second and no retries.** The SDK's own defaults are ten minutes and two retries, and its
  first retry sleeps three eighths to a half of a second before it starts. The client is built
  with `TIMEOUT_S` and none, and the stepper's own one-second bound holds whatever the client says,
  so a 429 or a 5xx is a turn the record calls an error.
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

## VERIFIED (read from OpenAI's Decisions guide and API reference on 2026-10-08, and from openai-python 1.66.0 and 3.22.1 on 2026-10-09)

| Thing | Value | What quackd does with it |
|---|---|---|
| The endpoint | `POST /v1/decisions`, in public beta since 2026-10-06 with general availability expected *"in the coming weeks"*, and `gpt-6-luna` *"the only model currently available"* | the row's address and model id; the client posts to `/decisions` under `<url>/v1` |
| The request | exactly `model`, `input`, `questions` and an optional `safety_identifier`; `input` is a string or a list of user messages | `model`, `input` and `questions` and nothing else. `input` is the state as one JSON string, the very string the stepper measures as `state_chars` |
| Questions | `predicate` (`instructions`, `name`), `choice` (`choices: [{value, description}]`, values typed, so the string `"true"` and the boolean `true` are different values) and `score` (`levels: [{label, description}]`, whose indices start at 0). The name is optional, and the guide says to give every question a unique one, which the API echoes | every question is named; a `noul` goes as a `predicate`, a choice's criteria as `choices` with every label a string and `escalate` last, and a score's as `levels` in the order they were written |
| Answers | `{answers, model, usage}`, the answers in question order, each naming its question or `null`: a `predicate` carries `probability`, a `choice` `choice`, `confidence` and `probabilities: [{value, probability}]`, a `score` `score`, `confidence` and `probabilities: [{value, label, probability}]` | read by name and never by position: an answer under a name quackd did not ask, or under none, is dropped, and the first under a name is the one kept. A `probability` becomes the `noul` the stepper reads, and a list of probabilities a mapping |
| Refusals | `{type: "refusal", name}` in place of one question's answer: *"The host may decline one question without disclosing its refusal score"* | the question is left unanswered, and its name is handed back beside the answers as `refused`, which ends the turn on the stepper's gate of that name and hands it to the model |
| `confidence` | not defined; the guide's examples have it below the top probability | carried as the stepper's confidence, field to field, and gated on the floors like any other row's, with the distribution recorded beside it |
| Usage | `input_tokens`, `input_tokens_details` (`cached_tokens`, `cache_write_tokens`), `output_tokens`, `output_tokens_details` (`reasoning_tokens`), `total_tokens` | passed through, so a turn is billed on the `input_tokens` OpenAI counted rather than on an estimate |
| Price | $0.10 per million input tokens, no output, cache-read or cache-write charges, and *"regional processing premiums and long-context input pricing multipliers apply"*, neither published for this endpoint ([the guide](https://developers.openai.com/api/docs/guides/decisions)) | `Price(input=0.10, output=0.0)`, dated 2026-10-08 on the record; a regional host is costed at this rate too, and `QUACKD_DECISION_PRICE` is how to say otherwise |
| Data | not used for training, kept 30 days in abuse-monitoring logs, eligible for zero data retention | nothing to do; [SECURITY.md](../../../SECURITY.md) says what a run sends |
| The SDK's resource | `client.decisions` arrived in openai 3.26.0, on 2026-10-06; the lock carries 3.22.1, which has none | not used. The client makes the SDK's generic request, `post(path, cast_to=object, body=..., options=...)`, which hands back the decoded JSON body unchanged in 1.66.0 and in 3.22.1, so no floor moves |
| The SDK's address | given none, it reads `OPENAI_BASE_URL`, else `https://api.openai.com/v1`, and it appends a request's path after making sure the address ends in a slash. Its own regional choice, in 3.22.1, names four hosts: `api`, `us.api`, `eu.api` and `ae.api.openai.com` | handed `<url>/v1` on every build, built from the parts the key was decided on; the key goes to those four hosts alone |
| The SDK's headers | it adds `OpenAI-Organization` and `OpenAI-Project` from `OPENAI_ORG_ID` and `OPENAI_PROJECT_ID`; 3.22.1 also adds every line of `OPENAI_CUSTOM_HEADERS`, split on newlines with the name before the first colon, and drops that variable's `Authorization` line only when the caller names one; an `Omit` in place of a value drops the header | names `Authorization` outright, omits every other name `OPENAI_CUSTOM_HEADERS` carries, read the same way, and omits the two account headers at any address that is not OpenAI's |
| The SDK's clocks | 600 s a request (5 s to connect) and two retries, the first after three eighths to a half of a second, or as long as a `Retry-After` asks up to two minutes | `timeout=TIMEOUT_S` and `max_retries=0`, with `asyncio.wait_for` around `decide()` in the stepper as the bound that holds |

## UNVERIFIED, and what quackd does about each

| Name | The assumption | What quackd does |
|---|---|---|
| `CONFIDENCE_MEANS_THE_SAME` | that OpenAI's `confidence` is comparable to the one the floors were set against. Nothing published says what it is, and nothing here has measured it | nothing automatic; `--decision-mode shadow` records what it would have chosen without letting it act, which is how a floor gets re-tuned |
| `LATENCY_UNDER_ONE_SECOND` | that it answers a robot's state inside the turn budget. OpenAI publish a ratio and no latency, and no request from quackd has ever timed it | `TIMEOUT_S` is 1.0 s, enforced around `decide()`; a turn that runs out goes to the model and the record says how long it waited. The SDK drops a connection that has sat idle five seconds, and in shadow mode the model's own call and a verb usually take longer than that between two of the stepper's requests, so each turn also records `idle_s`, the seconds since the stepper's previous request ended, which tells a turn that paid for opening a new connection from one that reused it |
| `CHOICE_BOUNDS` | the number of options a choice may carry. One reading of the reference says 2 to 255, and it could not be found again in any source | nothing; quackd always offers at least two (a call and `escalate`), and a request the API refuses is a turn the record calls an error |
| `ANSWER_NAMES_ECHOED` | that every answer carries the name it was asked under, as the guide says | nothing to rely on: answers are read by name alone, so one without its name is a question nobody answered, and the turn goes to the model |
| `NO_CACHE_DISCOUNT` | how cached input is billed. The guide charges nothing for cache reads or writes, and `usage` still reports `cached_tokens` | every input token is costed at the full rate, which is the higher reading |
| `REGIONAL_RATE` | the premium on the regional hosts and the long-context multiplier, which apply and are published for no Decisions row | the published rate everywhere OpenAI is reached; `QUACKD_DECISION_PRICE` for a rate of your own |
| `MODEL_ALIAS_MOVES` | whether `gpt-6-luna` names one fixed model or moves under a run | nothing; the record names the id quackd asked for |
| `GENERIC_POST_ACROSS_VERSIONS` | that the generic request keeps handing back the decoded body unchanged in every SDK between the floor and the cap. It was read in 1.66.0 and 3.22.1 | a body that is not a JSON object is a `ValueError`, a turn the record calls an error, and the model takes it |
| `BETA_SHAPE` | that the shapes above survive the beta | the reader is strict by name and lets an answer of a type it does not know go unanswered, so a renamed field costs a turn and never the run |

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
any other test that tries is stopped before it sends, and fails. Without the extra, `quackd doctor`
prints this row as `missing (quackd[openai])`, `OPENAI_API_KEY unset`, `gpt-6-luna` and
`https://api.openai.com`, and a run that names it says so once before it connects and carries on
without the stepper:

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
