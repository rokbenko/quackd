# local (any other System One server)

any other server that speaks /v1/systemone, at --decision-url. This is the row that exists to be
told where to go: no vendor, no upstream to link, no licence to name and no version to pin,
because what it describes is the wire format rather than an implementation of it. Any server that
answers `POST /v1/systemone` is reachable without quackd knowing anything else about it, which is
the whole point of the format being the abstraction; one that does not speak it is a plugin
instead, under the `quackd.decision_llms` entry point group ([writing a
plugin](../decision-llms.md#writing-a-plugin)). A decision LLM has no `upstream_api.py` the way an
adapter does, so every name quackd spells for this one lives in its row in
[`catalogue.py`](../../quackd/agent/decision/catalogue.py).

**Nothing here has ever answered a real robot.**

```bash
uv pip install "quackd[decision]"   # not run here: the System One client, the only install this row needs, and then start your own server on its own address
quackd run arm-grip-check --robot lerobot:mock --decision-llm local --decision-url http://127.0.0.1:9000 --decision-mode shadow   # not run here: no decision LLM is installed on this machine
```

## The row

| Field | Value |
|---|---|
| `name` | `local` |
| `backend` | `systemone`, so `SystemOneLLM` over `typesafe_sdk`, the same client every other server row on the table uses |
| `summary` | `any other server that speaks /v1/systemone, at --decision-url` |
| `install` | `--decision-url http://host:port (or QUACKD_DECISION_URL)` |
| `url` | `none`, and here that is a refusal rather than a default: the row exists to be told, so `make_decision_llm` stops the run and names both ways of supplying one |
| `key_env` | `none`, a server that wants no key: `systemone.py` sends the literal `local` instead, so a `TYPESAFE_API_KEY` sitting in the same `.env` is never posted to a port on your own machine |
| `model` | `none`, the server names its own, and `--decision-llm local:<id>` is how you say which |
| `price` | `none`, which is neither free nor unknown: it is a server you run, and `factory.resolve_decision_price` turns it into the self-hosted rate quackd already charges a local model at |
| `extra` | `decision`, installed with `quackd[decision]` |
| `sdk` | `typesafe_sdk`, the import that says whether that extra is here |

That is `PRESETS["local"]` in [`catalogue.py`](../../quackd/agent/decision/catalogue.py), and a
test reads the install line and the extra back off this page; there is no address and no model id
here for it to check, because supplying those with `--decision-url` and `--decision-llm
local:<id>` is the whole of what this row is for.

## What the wire format requires

| Part | What it requires |
|---|---|
| The request | `POST {your base url}/v1/systemone`, carrying `{state, model, questions}`. quackd's state is a flat mapping of short English strings -- `goal`, `success_when`, `body`, `where`, `now`, `camera`, `last`, `recent`, `tried`, `notes`, `flock` -- and its questions are four plain dicts of `{type, instructions, criteria}`, with the types `choice` and `noul` |
| No path on the address | the client appends `/v1/systemone` itself, so `--decision-url` takes a scheme, a host and a port and nothing else. A value with a path on the end produces `/v1/v1/systemone` and a 404 that names nothing, and a test holds every row in the catalogue to the same rule |
| The response | `{model, answers, usage}`. The `model` and `usage` keys must be present or the SDK's parser raises, though their contents may be null or empty. `answers` is keyed by the question names you were sent, and each one is a choice (`{choice, confidence}`, plus `probabilities` where you have them), a noul (`{noul}`) or a score |
| The key | quackd sends the literal word `local`, `NO_KEY` in [`systemone.py`](../../quackd/agent/decision/systemone.py), because the SDK requires a non-empty printable ASCII key and a server you run wants none. A hosted key sitting in your environment is never sent here |
| The model id | `--decision-llm local:<id>` names what the server should answer as. With none given the SDK substitutes its own default, `jev-latest`, which a server that validates ids may refuse, so name one |
| The budget, and the price | one second a turn: `TIMEOUT_S` is declared in [`base.py`](../../quackd/agent/decision/base.py) and enforced with `asyncio.wait_for` around `decide()` in [`stepper.py`](../../quackd/agent/decision/stepper.py), at the seam rather than left to the client, and at most one retry inside it. A server you run is costed at the self-hosted $0; a paid endpoint behind this row is the one case where that is wrong, and `QUACKD_DECISION_PRICE`, in the same syntax as `QUACKD_PRICE`, is how you say so |

## What to know before you point a robot at it

- **Nobody has read your server.** Every other row on the table had its README and its source read
  on a date its page names; this one by definition did not, so the assumptions below are
  assumptions about software quackd has never seen.
- **Its confidence is its own function.** quackd's floors -- brake 0.50, read 0.60, motion 0.85,
  confirm 0.90 -- are two numbers TypeSafe publish with two of quackd's own between them, all
  four shaped around Jev, and this server computes confidence by its own formula, whatever that
  turns out to be: an entropy ratio, a normalised top probability
  and a margin over the second place are three different meanings of 0.87. Read [how a turn is
  decided](../decision-llms.md#how-a-turn-is-decided), then stay in `--decision-mode shadow` until
  you have numbers of your own.

> [!WARNING]
> **Nothing in this shape authenticates anything.** quackd sends the word `local` in the key
> field, so whatever can reach the port can ask your server questions and read back what your
> robot is doing. A server bound to `0.0.0.0` rather than `127.0.0.1` is every interface, which on
> a shared network is everyone on it: keep it on loopback, or reach it through an ssh tunnel.

## VERIFIED (read from the typesafe-sdk 0.7.1 wheel on 2026-09-22)

| Thing | Value | What quackd does with it |
|---|---|---|
| Client | `AsyncTypeSafeClient(*, api_key, model, retry, timeout, headers, transport, http_client, base_url)`, every argument keyword-only, and `api_key` mandatory: the literal `local` is accepted | `systemone.py` builds one while the CLI is still parsing, before the robot connects, so a bad address is a sentence rather than a failed turn, and the key it hands over is that word |
| Address and path | `base_url` argument, else `TYPESAFE_BASE_URL`, else `https://api.typesafe.ai`, `rstrip("/")`; `/v1/systemone` is then a hardcoded constant appended to it, with no parameter to override it, and the issue asking for exactly that (its #7) was closed unresolved | `--decision-url` is passed explicitly, so this row never falls through to the hosted default. It is also why the address takes no path, and why a gateway serving the format at some other path is out of reach from this row |
| Retries and clocks | `RetryPolicy(max_retries=2, backoff_initial=0.5, backoff_max=5.0, backoff_jitter=0.25, respect_retry_after=True, timeout=30.0)`, where `timeout` is the budget for the whole retry sequence and the per-request default is a separate `10.0` s | `systemone.py` passes `max_retries=1`, `backoff_max=0.2` and `TIMEOUT_S` for both clocks, because setting only the sequence budget left a turn able to wait ten seconds against a page promising one |
| Questions and response | plain dicts fully supported beside the SDK's own `Choice`, `Noul` and `Score` classes, and a noul dict needs only `{"type": "noul"}`; on the way back the `model` and `usage` keys are strictly required or the parser raises, contents lenient, and there is no `latency` attribute despite the docs site listing one | `build_questions` emits dicts, so the same four questions reach a hosted API, a server on your machine and a model in this process without being rebuilt for each; the answers are read tolerantly, and `stepper.py` times the call itself and writes `latency_s` rather than trusting a field that is not there |
| Dependency | `httpx2` rather than `httpx` | any transport or timeout object you hand the client has to be an `httpx2` type, which is the one thing about this install that surprises people |

## UNVERIFIED, and what quackd does about each

| Name | The assumption | What quackd does |
|---|---|---|
| `NOBODY_HAS_CHECKED_THIS_ONE` | that the server you point it at is one quackd has ever seen | nothing, and that is the honest answer: every other row on the page had its README and its source read, and this one by definition did not |
| `WIRE_COMPATIBLE` | that its response keeps the shape `typesafe_sdk` parses | the SDK requires the `model` and `usage` keys present, contents may be null; quackd's own reader takes an answer as an object or as a plain mapping, and an answer it cannot read costs that turn as `gate: unreadable` or `gate: error` and hands it to the model rather than raising |
| `CONFIDENCE_MEANS_THE_SAME` | that its confidence is comparable to the one quackd's floors were set against. It is not: the floors (brake 0.50, read 0.60, motion 0.85, confirm 0.90) are two numbers TypeSafe publish with two of quackd's own set between them, all four Jev-shaped and none of them measured here, and this server computes confidence by its own formula | nothing automatic. `--decision-mode shadow` records what it would have chosen without letting it act, which is how the floors get re-tuned |
| `LATENCY_UNDER_ONE_SECOND` | that it answers inside the turn budget. Nobody has measured it against a robot | `TIMEOUT_S` is 1.0 s, enforced with `asyncio.wait_for` around `decide()` in `stepper.py` so it holds whatever the backend is; a turn that runs out escalates to the model and the record says how long it waited |
| `USAGE_REPORTED` | whether its token counts mean anything | where a count is missing or is not a measurement, quackd estimates the request at four characters to the token and marks the turn `usage_estimated`, and a self-hosted row is costed at $0 either way |
| `PYTHON_FLOOR` | that its own interpreter floor is its own business | nothing. `quackd[decision]` is a small HTTP client that runs on quackd's own Python, and your server runs in whatever environment you started it in |

## Status

It has never answered a real robot, and this row carries a refusal no other one does:
`--decision-llm local` with no `--decision-url` and no `QUACKD_DECISION_URL` stops the run and
names both ways of supplying one. That deserves the honest caveat, because it is not what a bare
machine shows you: the refusal is raised before the client is built and needs nothing installed,
but on a machine without `quackd[decision]` the run never reaches it, because availability is
checked first and the run says so once and carries on without a stepper. Install the extra and the
refusal is what you get. The client itself is exercised against a stub speaking the same wire
format (`tests/stub_decision_llm.py`, `tests/test_decision_llms.py`), which proves quackd's half
of the conversation and not yours; without the extra, `quackd doctor` prints this row as `missing
(quackd[decision])`, `none needed` for the key, `auto (the server names it)` for the model and
`unset: --decision-url or QUACKD_DECISION_URL` for the url. `--decision-mode shadow` is how that
changes.

## How to help

A server that speaks the format can stop being `local` and become a row and a page of its own,
which is four things and no code: the row, a page like this one, a row in the hub's table and a
line in the changelog ([CONTRIBUTING.md](../../CONTRIBUTING.md) has the checklist). One that does
not speak it is a plugin instead ([writing a plugin](../decision-llms.md#writing-a-plugin)).
Either way, run it in shadow mode on your own bench ([Measuring it
yourself](../decision-llms.md#measuring-it-yourself)) and report the agreement rate, the latency
and the answers that surprised you, and say which interface and port you ran the server on:
loopback and `0.0.0.0` are two different claims. A floor moves on a calibration curve ([How a turn
is decided](../decision-llms.md#how-a-turn-is-decided)).
