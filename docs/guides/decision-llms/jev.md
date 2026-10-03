# Jev (TypeSafe)

TypeSafe's Jev, hosted: the System One model the format is named after. It is made by TypeSafe
([typesafe.ai](https://typesafe.ai/)), the weights are closed and the only interface is an API, so
what quackd installs is not the model but its client,
[`typesafe-sdk`](https://pypi.org/project/typesafe-sdk/): MIT-licensed, read here at **0.7.1**
(published to PyPI 2026-09-21, read 2026-09-22), against the pinned model id `jev-1.13.0`. A
decision LLM has no `upstream_api.py` the way a robot adapter does: every name quackd spells for
it lives in its row in [`catalogue.py`](../../quackd/agent/decision/catalogue.py).

**Nothing here has ever answered a real robot.**

```bash
uv pip install "quackd[decision]"    # not run here
export TYPESAFE_API_KEY=...          # not run here: this is the only row that wants a key
quackd run arm-grip-check --robot lerobot:mock --decision-llm jev --decision-mode shadow    # not run here: no decision LLM is installed on this machine
```

## The row

| Field | Value |
|---|---|
| `name` | `jev` |
| `backend` | `systemone`, the shared `POST /v1/systemone` client rather than a module of its own |
| `summary` | `TypeSafe's Jev, hosted: the System One model the format is named after` |
| `install` | `quackd[decision] and TYPESAFE_API_KEY (typesafe.ai)` |
| `url` | `none`, the SDK holds TypeSafe's own address, and `TYPESAFE_BASE_URL` is how you move it |
| `key_env` | `TYPESAFE_API_KEY` |
| `model` | `jev-1.13.0` |
| `model_env` | `TYPESAFE_DEFAULT_MODEL`, honoured here alone because the SDK owns this row's address too |
| `price` | `Price(input=0.042, output=0.0, source="published")` |
| `extra` | `decision`, so `quackd[decision]` |
| `sdk` | `typesafe_sdk` |

That is `PRESETS["jev"]` in `catalogue.py`. A test reads the model id, the key variable, the
install line and the extra back off this page and compares them with the row, so a cell that
drifts fails the suite. `--decision-url` overrides the address for one run and `--decision-llm
jev:jev-1.14.0` the id.

## What it is

A hosted System One model, and the one every other row is imitating. TypeSafe publish no parameter
count, no architecture and no weights: it is an API and nothing else, so the hardware it needs is
a network connection and a key, and the only thing running on your machine is an HTTP client. Text
only, with **32k tokens for the state plus the longest question, under a 64k request cap**, so a
turn whose state will not fit is a gate on the record rather than a truncation nobody sees. The
latency figure is theirs: **TypeSafe's own worked example puts one call at 0.114 s and $0.000081**
against 8.566 s and $0.013880 for a language model, and their front page turns that into a
headline of **193.6x faster and 444.6x cheaper**. The two do not agree, because the example
divides out to 75x and 171x, so the headline is a range over their tasks rather than a constant
and nothing here repeats either as a quackd measurement. The interpreter floor is the client's:
`typesafe-sdk` 0.7.1 needs **Python 3.10 or newer**.

## What to know before you point a robot at it

- **The path is not configurable.** `SYSTEM_ONE_PATH = "/v1/systemone"` is a hardcoded constant in
  `typesafe_sdk/_core/constants.py`, concatenated onto the base URL, and the docs site agrees. A
  gateway serving Jev elsewhere (the OpenRouter Decisions API) is out of reach by repointing it.
- **The key is mandatory and validated.** Without one, and without `TYPESAFE_API_KEY`, the client
  raises `TypeSafeError` at construction; the validator rejects empty, non-ASCII, non-printable
  and whitespace-containing keys, and accepts the literal `local`, which is what every self-hosted
  row sends instead. This being the only keyed row is why `QUACKD_DECISION_URL` is not read for
  it.
- **Two clocks, meaning different things.** `RetryPolicy.timeout` (30.0 s) is the budget for the
  *whole retry sequence*; the client's own `timeout=` (default 10.0 s) is the budget for *one
  request*. Setting only the first left a turn able to wait ten seconds, which `timeout=TIMEOUT_S`
  and `wait_for` fixed.
- **The id is pinned to `jev-1.13.0` rather than `jev-latest`**: a run whose decision LLM changed
  under it describes a model that is no longer the one that answered, and `jev:jev-latest` asks
  for a moving one.
- **This is the model the floors were shaped around, which is not the same as the model they were
  measured on.** Of the four on [How a turn is
  decided](../decision-llms.md#how-a-turn-is-decided), two are numbers TypeSafe publish: 0.50,
  which they call genuinely unsure, and 0.90, their "high stakes, proceed with confirmation". The
  read floor at 0.60 and the motion floor at 0.85 are quackd's own, set between those two. So this
  row inherits nobody else's calibration, and still no measured one: they publish no formula
  behind the probability, and say the right thresholds are domain-specific.

> [!WARNING]
> **`--decision-url` on this row changes what is sent as well as where it goes.** A keyed row
> reached at an address somebody typed is no longer the hosted service, so `systemone.py` sends
> the word `local` instead of the key: posting a company's API key to whatever is listening on
> `http://127.0.0.1:8009` is the thing `SECURITY.md` says must not happen.

## VERIFIED (read from its README and source on 2026-09-22, typesafe-sdk 0.7.1)

| Thing | Value | What quackd does with it |
|---|---|---|
| Constructor | `AsyncTypeSafeClient(*, api_key, model, retry, timeout, headers, transport, http_client, base_url)`, all keyword-only, on `httpx2>=2.0.0` (not `httpx`), `pydantic>=2.12` and `tenacity>=9`; both clients are context managers, and never closing one emits no warning | `systemone.py` builds it while the CLI is still parsing, so a bad install is a sentence before the robot connects, and `quackd[decision]` is this client and nothing else |
| Key handling | `TypeSafeError` at construction with no key and no `TYPESAFE_API_KEY`; the validator rejects empty, non-ASCII, non-printable and whitespace-containing keys and accepts `local` | `doctor` reports the variable unset, and a run that names this row without a key says so once before it connects to the robot and carries on without the stepper: `DecisionMissingKey` is what `decision_llm_is_available` puts in that sentence, and is never raised. `NO_KEY = "local"` in `systemone.py` is what every keyless row sends |
| Address | argument, else `TYPESAFE_BASE_URL`, else `https://api.typesafe.ai`, `rstrip("/")`, with `SYSTEM_ONE_PATH = "/v1/systemone"` from `typesafe_sdk/_core/constants.py` appended by string concatenation | the row's `url` is `none` and quackd passes `base_url` only when a flag gave one; `--decision-url` takes a scheme, a host and a port and no path |
| Two timeouts | `RetryPolicy` frozen: `max_retries=2, backoff_initial=0.5, backoff_max=5.0, backoff_jitter=0.25, http_statuses={408, 429, 500..599}, respect_retry_after=True, timeout=30.0`, that last one the budget for the whole retry sequence; the per-request clock is the client's own `timeout=`, default `10.0` s | overridden with `max_retries=1, backoff_max=0.2, timeout=1.0` because one retry fits a one-second turn and two do not, plus `TIMEOUT_S` on the client itself and `asyncio.wait_for` around `decide()` |
| The call | `system_one(state, questions, *, model, retry, timeout, extra_headers, extra_body, response_model)`, async; the SDK always sends `model`, substituting its own default when the call names none, and its own headers (`Authorization: Bearer <key>`, `User-Agent: typesafe-sdk/0.7.1`, `X-TypeSafe-SDK`, `X-TypeSafe-Runtime`, `X-TypeSafe-Retry-Count` on retries) overwrite anything passed in `headers=` | one request a turn carrying all four questions, with the row's id named explicitly so the record says what quackd asked for; quackd passes no headers, because one passed here would be silently discarded |
| Plain dicts are questions | `Noul`, `Choice` and `Score` plus the three TypedDicts; a noul dict needs only `{"type": "noul"}`, while choice and score require criteria | the four questions are built once as dicts and reach every backend unrebuilt |
| Response | `SystemOneResponse` has exactly `model`, `usage` and `answers`, plus `nouls`, `choices` and `scores` views; missing `model` or `usage` raises `TypeSafeAPIResponseValidationError`, though contents are lenient and `usage: {}` parses. There is no `latency` attribute, contrary to the docs site, which is wrong there. Answers parse `strict=True`: a noul spelled `"0.9"` is rejected, an int `1` widens to `1.0`, and unknown answer types are dropped with a warning rather than failing the response | `stepper._answer` reads it as an object or as a plain mapping and quackd times the call itself into `latency_s`; a partly answered turn costs the turn as `gate: unreadable` rather than moving the body on two questions nobody answered |
| Error taxonomy | `TypeSafeError`, `TypeSafeAPIError(status, body, headers, endpoint, .request_id)` with per-status subclasses (400, 401, 403, 404, 422, 429, 5xx), plus connection, timeout and response-validation errors | `Stepper.advise` catches everything and records `gate: error`; a decision LLM may never end a run |
| Price | $0.042 per million input tokens, output not charged ([their models page](https://docs.typesafe.ai/models), read 2026-09-21) | the only row with a published rate; carried as `Price` and stamped on `run_start` beside the name, model and url |

## UNVERIFIED, and what quackd does about each

| Name | The assumption | What quackd does |
|---|---|---|
| `WIRE_COMPATIBLE` | that the answer keeps the shape `typesafe-sdk` parses | the SDK requires the `model` and `usage` keys present, contents allowed to be null; quackd's own reader takes an object or a plain mapping, and an answer it cannot read costs the turn as `gate: unreadable` or `gate: error` and hands it to the model rather than raising |
| `CONFIDENCE_MEANS_THE_SAME` | that its confidence is comparable to the one quackd's floors were set against. Here it is the same model those numbers came from, which is as close as this gets, and TypeSafe still publish no formula behind the probability | nothing automatic; `--decision-mode shadow` records what it would have chosen without letting it act, which is how the floors get re-tuned |
| `LATENCY_UNDER_ONE_SECOND` | that 0.114 s on TypeSafe's own task holds on a robot's state over your own network. Nobody has measured it against a robot | `TIMEOUT_S` is 1.0 s, enforced with `asyncio.wait_for` around `decide()` in `stepper.py` so it holds whatever the backend is; a turn that runs out escalates to the model and the record says how long it waited |
| `USAGE_REPORTED` | whether the token counts come back on a real call. Both are typed optional and documented as absent where the API did not report one | where a count is missing or is not a measurement, quackd estimates the request at four characters to the token and marks the turn `usage_estimated`; at $0.042 per million the money either way is a rounding error, and the flag is there so nobody reads the estimate as an invoice |
| `PYTHON_FLOOR` | that the SDK's own 3.10 floor is the SDK's problem | nothing; `quackd[decision]` is a small HTTP client that runs on quackd's own Python, and the model runs on TypeSafe's machines |
| `PATH_IS_HARDCODED` | that `/v1/systemone` stays where it is | nothing it can do; a gateway serving Jev at another path (the OpenRouter Decisions API) is out of reach until the SDK makes the path configurable, and its issue #7 asking for exactly that was closed unresolved |

## Status

It has never answered a real robot, and this page says so until somebody reports that it has. The
client is exercised against a stub rather than against TypeSafe: `tests/stub_decision_llm.py`
stands in for the server and `tests/test_decision_llms.py` drives the row, the key rule, the two
clocks and every gate an unreadable answer can fall into. Without the extra, `quackd doctor`
prints this row as `missing (quackd[decision])`, `TYPESAFE_API_KEY unset`, `jev-1.13.0`, and an
empty url, which is the hosted address the SDK owns. `--decision-mode shadow` is how that status
changes.

## How to help

Run it in shadow mode on your own bench and report what came back. The commands and the rows to
read are in [Measuring it yourself](../decision-llms.md#measuring-it-yourself), and what is worth
an issue is the agreement rate, the real latency against a robot's state rather than TypeSafe's
0.114 s on TypeSafe's own task, and the answers that surprised you. Say which base URL you pointed
the client at and which model id answered, because `TYPESAFE_BASE_URL`, `--decision-url` and
`TYPESAFE_DEFAULT_MODEL` all move those and a number without them is not reproducible. A floor
moves on calibration rather than opinion, confidence bucketed against agreement per verb class, as
[How a turn is decided](../decision-llms.md#how-a-turn-is-decided) sets out. Two of the four are
numbers TypeSafe publish and the brake and the confirm gate sit exactly on them, so evidence
that they are wrong *here* is the most load-bearing thing anybody can send.
