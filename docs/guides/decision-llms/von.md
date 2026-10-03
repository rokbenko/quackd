# Von

Von, self-hosted: a 395M encoder, about 18 ms on a GPU, runs on CPU too. It is
[wfzyx/von](https://github.com/wfzyx/von), Apache-2.0, on PyPI as `von-sdk`, read at `HEAD` on
2026-09-22 on its `master` branch, because the repository carries no tag and no release to
pin to. The names quackd spells
for it live in its row in [`catalogue.py`](../../quackd/agent/decision/catalogue.py): a decision
LLM has no `upstream_api.py`, because there is nothing to import and the wire is `POST
/v1/systemone` like every server here.

**Nothing here has ever answered a real robot.**

```bash
pip install von-sdk && von serve --host 127.0.0.1 --port 8000   # not run here
quackd run arm-grip-check --robot lerobot:mock --decision-llm von --decision-mode shadow   # not run here: no decision LLM is installed on this machine
```

## The row

| Field | Value |
|---|---|
| `name` | `von` |
| `backend` | `systemone`, so it is reached with `typesafe_sdk`, the protocol's own client |
| `summary` | `Von, self-hosted: a 395M encoder, about 18 ms on a GPU, runs on CPU too` |
| `install` | `pip install von-sdk && von serve --host 127.0.0.1 --port 8000`. The `--host` is quackd's, not its README's: its own default is `0.0.0.0` |
| `url` | `http://127.0.0.1:8000` |
| `key_env` | `none`, a server that wants no key: the client sends the literal word `local` instead, so a hosted `TYPESAFE_API_KEY` sitting in the same `.env` is never posted to a port on your own machine |
| `model` | `von-latest` |
| `price` | `none`, which is neither free nor unknown: it is a server you run, and `factory.resolve_decision_price` turns it into the self-hosted rate quackd already uses for a model on your own machine |
| `extra` | `decision`, installed as `quackd[decision]` |
| `sdk` | `typesafe_sdk`, the import that says whether that extra is present |

That is `PRESETS["von"]` in [catalogue.py](../../quackd/agent/decision/catalogue.py), and a test
reads the url, the model id, the key, the install line and the extra back off this page to compare
them with the row. `--decision-url` overrides the address and `--decision-llm von:<id>` overrides
the model id, though on this server an id picks nothing and is not even handed back: the answer
always names `von-1.1.0`, whatever was asked for. See below.

## What it is

ModernBERT-Large, 395M parameters, about 1.5 GB of weights pulled from the Hugging Face repository
`wfzyx/von` (`VON_HF_REPO` in `option_marker_backend.py`) at first inference. That repository id
is the weights, not a value to put in the wire `model` field. Device auto-detect runs
cuda, then mps, then cpu; `--device` also takes rocm, dml or cpu. Its README claims about 18 ms on
a GPU and publishes no CPU figure at all. Its own interpreter floor is Python 3.12, its dependency
set is heavy for a 395M model, and it is Apache-2.0. It also ships an in-process API
(`von.decide`, `von.judge`, `von.rate`, `von.system_one`, `VonClient(local=True)`) and CLI
one-shots that need no server; quackd uses neither, which matters.

## What to know before you point a robot at it

> [!WARNING]
> **`von serve` with no flags binds `0.0.0.0` on port 8000.** Every interface, which on
> a laptop on a shared network means everyone on that network, and nothing authenticates it unless
> you set `VON_API_KEY`. Its README's `--host 0.0.0.0 --port 8000` line restates those defaults
> rather than hardening anything. Pass `--host 127.0.0.1`, or put it behind something, first.

- **A bench that did not go over HTTP is still not this.** Both entry points load the same
  backend, so the model is not the variable; the FastAPI handler, the singleton engine and the
  network are, and they are the whole of what quackd adds to a forward pass.
- **This server computes confidence by its own formula**: normalised Shannon entropy over the
  option scores, on `choice` and `score` only, with a noul carrying none at all. quackd's floors
  (brake 0.50, read 0.60, motion 0.85, confirm 0.90) are two numbers TypeSafe publish with two
  of quackd's own between them, all four shaped around Jev, and this row inherits every one of
  them unmeasured; see [how a turn is decided](../decision-llms.md#how-a-turn-is-decided).
- **Port 8000 is contested.** The `opendecision` row defaults to it, and quackd's own vLLM *pilot*
  preset defaults to `http://localhost:8000/v1`; two of them at once needs one moved with
  `--decision-url`.
- **Nothing here caps the state.** No tokenizer call in the serving path passes `truncation` or
  `max_length`, and there is no request size limit and no rate limit in front of it, so an
  over-long state is the tokenizer's problem rather than a 4xx you can read.

## VERIFIED (read from its README and source on 2026-09-22, at `master` of wfzyx/von)

| Thing | Value | What quackd does with it |
|---|---|---|
| Serve defaults | `--host` default `"0.0.0.0"`, `--port` default `8000`, the click decorators in `src/von/cli.py` | quackd's row says `http://127.0.0.1:8000`, which is where it is once you bind it properly; `--decision-url` moves it |
| Packaging | Apache-2.0; `requires-python >=3.12` in `pyproject.toml`, in the PyPI metadata and in `.python-version`, so it will not install on 3.11; torch>=2.0, transformers>=5.0, accelerate, fastapi, uvicorn, click, httpx | nothing: `quackd[decision]` is a small HTTP client on quackd's own Python, and the server runs in whatever environment you started it in |
| Auth | `os.environ.get("VON_API_KEY")` read per request in `server.py`: unset or empty runs no check at all, set demands `Authorization: Bearer <VON_API_KEY>` or returns 401. Its own Python client falls back to `TYPESAFE_API_KEY`. CORS allows every origin by default and turns credentials off while it does: `allow_credentials=not _cors_wildcard`, so setting an explicit `VON_CORS_ORIGINS` list is what enables them | quackd sends the word `local` in the key field on every keyless row, so a hosted key beside it in the environment is never sent here |
| The wire `model` is accepted, ignored, and deliberately not reflected | `model: str = Field(default="von-latest")` in `server.py`: optional, never validated, no unknown-model error ever returned. `server.py` hands it to `engine.evaluate(..., model=req.model)`, which throws it away: `resolved_model = f"von-{VON_VERSION}.0"` before the backend is called, with an upstream comment saying that echoing a caller's id back would let a stale client read a response labelled as a model that no longer exists. So the answer always names `von-1.1.0`. `GET /v1/models` advertises `von-latest`, `von-1.1.0` and `jev-latest` in both a `models` array and an OpenAI-style `data` array, though only the first is a name the engine would accept as a backend | quackd sends `von-latest` and records what it asked for, so on this row the record's id and the id the server put on the answer are two different strings. Neither picks anything. It never reads `/v1/models`: the id comes from the row rather than from discovery |
| One backend, whichever way you come in | `VonEngine.__init__` takes `backend_name: str = "von-1.1"` and it is `get_instance` that falls back to `os.environ.get("VON_BACKEND", f"von-{VON_VERSION}")`; either way every name in `VON_CURRENT_ALIASES` (`von-1.1`, `1.1`, `von`, `default`, `latest`, `von-latest`) loads `OptionMarkerBackend`, and there is no other. `von serve`'s flag is `--model`, bound to `backend`, a `click.Choice` over those same aliases, so the CLI and a library import land on the same model | quackd is the HTTP entry point and gets that one backend, so an id in `--decision-llm von:<id>` changes the string in the record and nothing about what answers |
| Response shape, and where a confidence lives | three top-level fields and no more: `model`, `answers`, `usage`. No latency field. `NoulAnswer` in `types.py` is exactly `{type: "noul", noul: float}`, so a noul carries no confidence and no probabilities; `probabilities` is on choice and score, `legend` on score only. The confidence itself is entropy-based, the code normalising Shannon entropy | quackd times the call itself and writes `latency_s`; `done` and `need_human` compare the raw noul against 0.5, and a choice's confidence is compared against `FLOORS` in `stepper.py`, two of them numbers TypeSafe publish and two of them quackd's own, none of them this server's |
| `usage` is present and fabricated | a character heuristic rather than tokeniser counts: `input_tokens = max(1, len(state_str)//4) + max(1, total_instruction_chars//4)`, `output_tokens = len(answers)` | quackd reads `input_tokens` as a measurement, so the turn is recorded without `usage_estimated`; harmless only because a self-hosted row is costed at $0. Do not bill or budget on these |
| No cap anywhere in front of it | no tokenizer call in the serving path passes `truncation` or `max_length`, and there is no request size limit and no rate limit; `CONTEXT_EXPANSION.md` advertises 2,048 native tokens with a roadmap to 32k, and the README names neither figure | nothing: quackd's `state_too_large` gate is about quackd's own budget, and quackd records `state_chars` and `state_tokens_est` on every stepper turn, so a shadow run says how much state was in play |
| Concurrency, and how errors come out | `VonEngine` is a process-wide singleton and the FastAPI handler is `async def` calling blocking torch inference inline: no thread pool, no batching, no queue. Any engine exception becomes raw FastAPI `422 {"detail": "<str(exc)>"}` rather than a TypeSafe error object, auth failure `401 {"detail": ...}`; it also serves `GET /health` and `GET /` | one request per turn, so one robot is fine and more than one wants more than one process. The SDK raises, `Stepper.advise` catches everything, and the turn is recorded as `gate: error` and handed to the model: a decision LLM may never end a run |
| Its own numbers disagree | a Key Capabilities bullet claims "91.23% accuracy on adversarial multi-hop reasoning benchmarks, surpassing published commercial alternatives", while its own benchmark table three paragraphs later puts Von at 72.0% macro against TypeSafe's Jev at 96.6% | quackd quotes the table: a table with a comparison in it shows its working and a capabilities bullet does not, and where a project's own two numbers disagree that is the one to believe |

## UNVERIFIED, and what quackd does about each

| Name | The assumption | What quackd does |
|---|---|---|
| `WIRE_COMPATIBLE` | that Von's response keeps the shape `typesafe_sdk` parses | the SDK requires the `model` and `usage` keys present, though their contents may be null; quackd's own reader takes an answer as an object or as a plain mapping, and an answer it cannot read costs the turn as `gate: unreadable` or `gate: error` and hands it to the model rather than raising |
| `CONFIDENCE_MEANS_THE_SAME` | that Von's entropy confidence is comparable to the one quackd's floors were set against. It is not: the floors (brake 0.50, read 0.60, motion 0.85, confirm 0.90) are two numbers TypeSafe publish with two of quackd's own set between them, all four Jev-shaped and none of them measured here, and this server computes confidence by its own formula | nothing automatic. `--decision-mode shadow` records what it would have chosen without letting it act, which is how the floors get re-tuned |
| `LATENCY_UNDER_ONE_SECOND` | that it answers inside the turn budget. The 18 ms is a GPU figure from its own README, and nobody has measured it against a robot | `TIMEOUT_S` is 1.0 s, enforced with `asyncio.wait_for` around `decide()` in `stepper.py` so it holds whatever the backend is; a turn that runs out escalates to the model and the record says how long it waited |
| `USAGE_REPORTED` | whether its token counts mean anything, given that they are a character heuristic | where a count is missing or is not a measurement, quackd estimates the request at four characters to the token and marks the turn `usage_estimated`, and a self-hosted row is costed at $0 either way |
| `PYTHON_FLOOR` | that Von's 3.12 floor is quackd's problem. Its own interpreter floor is its own | nothing. `quackd[decision]` is a small HTTP client that runs on quackd's own Python; the server runs in whatever environment you start it in |
| `READ_AT_A_MOVING_HEAD` | that what was read on 2026-09-22 is what you will install. There is no tag and no release, so `master` is the only thing to cite, and the backend layout on it has changed before | nothing automatic: the row carries an address and a model id and no version, and `quackd doctor` reports what answers rather than what it expected. A shadow run is the check |
| `PORT_COLLISION` | that nothing else on the machine holds 8000 | nothing automatic: a refused connection or an answer from the wrong service is a `gate: error` and the model's turn. `--decision-url` is how you say where it really is |

## Status

It has never answered a real robot. The client is exercised against a stub speaking the same wire
format (`tests/stub_decision_llm.py`, `tests/test_decision_llms.py`), which proves quackd's half
of the seam and nothing about Von's. Without the extra, `quackd doctor` prints this row as
`missing (quackd[decision])`, `none needed` for the key, `von-latest` for the model and
`http://127.0.0.1:8000` for the address. `--decision-mode shadow` is how that changes: it asks
every turn, records what it would have chosen, and lets nothing it says reach a servo.

## How to help

Run it in shadow mode on your own bench ([measuring it
yourself](../decision-llms.md#measuring-it-yourself)) and report the agreement rate, the latency
you actually saw on your own hardware, and the answers that surprised you. Say which interface and
which port you served it on, because `0.0.0.0` and 8000 are both defaults worth knowing you kept.
A confidence floor moves on a calibration curve rather than on a feeling ([how a turn is
decided](../decision-llms.md#how-a-turn-is-decided)), and [CONTRIBUTING.md](../../CONTRIBUTING.md)
says where to put one.
