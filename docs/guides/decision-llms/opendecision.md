# OpenDecision

OpenDecision, self-hosted: a zero-shot encoder that runs without a GPU. It is
[deepanwadhwa/OpenDecision](https://github.com/deepanwadhwa/OpenDecision), Apache-2.0, read at
`main` on 2026-09-22 against the `0.1.1` release uploaded to PyPI on 2026-09-20, which its own
README calls a developer preview. Every name quackd spells for it lives in its row in
[`catalogue.py`](../../quackd/agent/decision/catalogue.py): a decision LLM has no
`upstream_api.py`, because there is nothing here to import, only a port to post to.

**Nothing here has ever answered a real robot.**

```bash
pip install OpenDecision && opendecision serve   # not run here: wants Python >= 3.13, and the first serve downloads the model before it listens
uv pip install "quackd[decision]"   # not run here: the client quackd posts with
quackd run arm-grip-check --robot lerobot:mock --decision-llm opendecision --decision-mode shadow   # not run here: no decision LLM is installed on this machine
```

## The row

| Field | Value |
|---|---|
| `name` | `opendecision` |
| `backend` | `systemone`, so `POST /v1/systemone` over `typesafe_sdk`, the protocol's own client |
| `summary` | `OpenDecision, self-hosted: a zero-shot encoder that runs without a GPU` |
| `install` | `pip install OpenDecision && opendecision serve` |
| `url` | `http://127.0.0.1:8000`, its own default host and port |
| `key_env` | `none`, a server that wants no key: the client sends the literal `local` instead, so a hosted `TYPESAFE_API_KEY` sitting in the same `.env` is never posted to a port on your own machine |
| `model` | `opendecision`, accepted and ignored by the server, sent anyway so the record says what quackd asked for |
| `price` | `none`, which is not free and not unknown: it is a server you run, and `factory.resolve_decision_price` costs it at the self-hosted rate quackd already uses for a model on your own machine |
| `extra` | `decision`, installed as `quackd[decision]` |
| `sdk` | `typesafe_sdk`, the import whose presence says whether that extra is here |

That is `PRESETS["opendecision"]` in [`catalogue.py`](../../quackd/agent/decision/catalogue.py),
and a test reads the address, the model id, the key, the install line and the extra back off
this page. `--decision-url` overrides the address;
`--decision-llm opendecision:<id>` the model id.

## What it is

A zero-shot natural language inference engine, not a decision-trained model: it scores your labels
against the state with an off-the-shelf NLI checkpoint,
`MoritzLaurer/ModernBERT-large-zeroshot-v2.0` by default, about 400M parameters, downloaded during
startup rather than lazily. No GPU is needed, which is why this Apache-2.0 row is in the
catalogue, and it wants **Python >= 3.13**, the biggest thing standing between it and a reader:
quackd's own venv is 3.11 and its CI runs 3.11 and 3.12, so it needs a 3.13 environment of its
own. Nobody publishes a latency for it, its README included.

## What to know before you point a robot at it

- **Its confidence is its own function.** `_distribution_confidence()` is `1 - (entropy /
  max_entropy)` over the distribution it returns, and its source says the definition is
  OpenDecision's rather than TypeSafe's, while quackd's floors are two of TypeSafe's numbers
  for Jev ([How a turn is decided](../decision-llms.md#how-a-turn-is-decided)).
- **A choice is not one forward pass**: two compiler passes plus an adjudicator when they
  disagree, and questions answered serially, so quackd's four-question turn is 6 to 8 passes
  against a 1.0 s timeout.
- **Port 8000 is contested**: `von` defaults there, as does quackd's vLLM pilot preset;
  `--decision-url` moves one.

> [!WARNING]
> **Nothing authenticates this server**: no header is read, no CORS middleware, no rate limit, no
> request id. The default `--host 127.0.0.1` is the only thing between a network and an
> unauthenticated torch pipeline, so `--host 0.0.0.0` on a shared network hands the robot's
> decisions to everyone on it. Keep it on loopback.

## VERIFIED (read from its README and source on 2026-09-22, at `0.1.1` and `main`)

| Thing | Value | What quackd does with it |
|---|---|---|
| Release and interpreter | `0.1.1` on PyPI, uploaded 2026-09-20, Apache-2.0, a developer preview in its own README; `requires-python >= 3.13`, with `torch>=2.14`, `transformers>=5.17` and `fastapi>=0.141` under it | the pin this page was read at. quackd's venv is 3.11 and its CI is 3.11 and 3.12, so this row needs a 3.13 environment of its own, every time. The display name is `OpenDecision`, PEP 503 makes `pip install opendecision` resolve identically, and the import name is lowercase |
| Weights and device | `MoritzLaurer/ModernBERT-large-zeroshot-v2.0`, about 400M parameters, chosen at startup by `--model` or `OPENDECISION_MODEL` and downloaded then rather than lazily; the device auto-picks `cuda`, then `mps`, then `cpu` | the first serve blocks on the download, so start it once before a robot is waiting on it; the CPU path is why the catalogue's summary says without a GPU |
| Bind and surface | `--host` defaults to `127.0.0.1` and `--port` to `8000` (`src/opendecision/cli.py`), and bare `opendecision` prints help and exits 0; `GET /health`, `POST /v1/systemone`, `POST /v1/documents/decide`, `GET /docs`, `GET /openapi.json`, plus an extra `relation` question type | the row's `url` is `http://127.0.0.1:8000` and the install line ends in `serve` because nothing listens without it (`python -m opendecision serve` works too). The SDK posts `/v1/systemone` and nothing else; the documents endpoint and the extra type are additive, and ignoring them leaves TypeSafe's own surface |
| Auth | none anywhere: `api_key`, `authorization`, `bearer`, `Depends`, `middleware`, `security` and `cors` return zero hits across its app, schemas, cli, engine, documents, rules and evidence modules; its own example constructs `TypeSafeClient(api_key="local", base_url="http://127.0.0.1:8000")` | the row has no `key_env`, so `systemone.py` sends the literal `local` in the key field, which is what its own example expects, and never a hosted key |
| Request and response contract | the answer is top level `{model, answers, usage{input_tokens, output_tokens}}`; a choice `{type, choice, probabilities, confidence}`; a noul a bare float with no confidence; a score with `score`, `legend`, `probabilities` and `confidence`. The request is validated more strictly than Jev in places, all 422: a question may not be empty, a choice needs at least 2 criteria, a score at least 2 and unique, and a noul's criteria must be omitted or exactly `{true, false}` | verified field for field against docs.typesafe.ai: the most faithful shape on this page, and nothing about it needs tolerating. quackd builds a choice of at least two labels (a verb plus `escalate`) and nouls with no criteria at all, so all four validation rules are cleared by construction |
| What the answer says about itself | the request's `model` is optional in `src/opendecision/api/schemas.py`, commented "Optional for now. This will help later when we point the official SDK at OpenDecision" and never read by the handler, while the response echoes the server's own Hugging Face id instead; `usage.output_tokens` is hardcoded `0` on every response, and `usage.input_tokens` is `len(tokenizer(json.dumps(the request)))`, the tokenised length of the serialised request, whose docstring says "This is NOT currently intended to represent actual transformer compute" | quackd sends `opendecision` and asserts nothing about what comes back, so the run record says what it asked for, and a client that pins an id and checks the one returned sees a different string. A count is used where there is one and estimated at four characters to the token where there is not, marking the turn `usage_estimated`; a self-hosted row is costed at $0 either way |
| Confidence | `_distribution_confidence()`, which is `1 - (entropy / max_entropy)` over the returned distribution, and its README: "Treat the model scores as uncalibrated. Evaluate the model and thresholds on your own data before using the results in an automated decision process." | compared against floors two of which TypeSafe publish and two of which are quackd's own, which is precisely what `--decision-mode shadow` exists to check before a floor is moved |
| Throughput | `engine.choice()` runs two compilers on different premise/hypothesis templates plus a third frozen adjudicator over the two disputed labels when they disagree, and questions in one request run in a serial for-loop with no batching; both endpoints are plain `def`, so FastAPI dispatches them to its threadpool over one engine and one Hugging Face pipeline on `app.state` with no lock, in a single process with no `--workers` | four questions a turn, two of them choices (`next_verb` and `feasible`), so 6 to 8 sequential passes inside the 1.0 s `TIMEOUT_S` in `base.py`; one request a turn is one robot's worth of traffic, and the only shape this server is safe at |
| Benchmarks | 17 of 17 insurance facts, 9 of 10 GDPR questions and a Doom demo, and its README says "These are fixed-seed recordings. No win rate has been measured." | nothing quackd can quote as an accuracy or a latency; the bench you run on your own bodies is the one that counts |

## UNVERIFIED, and what quackd does about each

| Name | The assumption | What quackd does |
|---|---|---|
| `WIRE_COMPATIBLE` | that its response keeps the shape `typesafe_sdk` parses, which it does more faithfully than anything else here, on one reading of one release | the SDK requires the `model` and `usage` keys present and tolerates null contents; quackd's own reader takes an answer as an object or as a plain mapping, and one it cannot read costs that turn as `gate: unreadable` or `gate: error` and hands it to the model rather than raising |
| `CONFIDENCE_MEANS_THE_SAME` | that its confidence is comparable to the one quackd's floors were set against. It is not: the floors (brake 0.50, read 0.60, motion 0.85, confirm 0.90) are two numbers TypeSafe publish with two of quackd's own set between them, all four Jev-shaped and none of them measured here, and this server computes confidence by entropy over its own distribution | nothing automatic. `--decision-mode shadow` records what it would have chosen without letting it act, which is how the floors get re-tuned |
| `LATENCY_UNDER_ONE_SECOND` | that a four-question turn, two of them multi-pass choices on a CPU, answers inside the turn budget. Nobody has measured it against a robot | `TIMEOUT_S` is 1.0 s, enforced with `asyncio.wait_for` around `decide()` in `stepper.py` so it holds whatever the backend is; a turn that runs out escalates to the model and the record says how long it waited |
| `MODEL_FIELD_IGNORED` | that the model quackd names is the model that answers | it is not: the server's is chosen at startup, so the record says what quackd asked for rather than what ran, and `--model` on the server is the only place the two can be made to agree |
| `USAGE_REPORTED` | whether a tokenised request length means anything as a token count | where a count is missing or is not a measurement, quackd estimates the request at four characters to the token and marks the turn `usage_estimated`, and a self-hosted row is costed at $0 either way |
| `NO_LOCK_ON_THE_PIPELINE` | that one robot at a time is enough | quackd sends one request a turn, so a single robot is fine and a flock pointed at one instance is not |
| `PYTHON_FLOOR` | that a 3.13-only server is one you can actually start; its interpreter floor is its own | nothing. `quackd[decision]` is a small HTTP client that runs on quackd's own Python, and the server runs in whatever environment you start it in, which here has to be a separate one |

## Status

It has never answered a real robot: the client is exercised against a stub that speaks the same
wire format (`tests/stub_decision_llm.py`, `tests/test_decision_llms.py`), which proves quackd's
half of the conversation and not OpenDecision's. Without the extra, `quackd doctor` prints this
row as `missing (quackd[decision])`, `none needed` for the key, `opendecision` for the model and
`http://127.0.0.1:8000` for the url. `--decision-mode shadow` changes that.

## How to help

Run it in shadow mode on your own bench ([Measuring it
yourself](../decision-llms.md#measuring-it-yourself)) and report the agreement rate, the latency
and the answers that surprised you, and say which interface and port you served it on: loopback
and `0.0.0.0` are two different claims. A floor moves on a calibration curve ([How a turn is
decided](../decision-llms.md#how-a-turn-is-decided)), and [CONTRIBUTING.md](../../CONTRIBUTING.md)
says where.
