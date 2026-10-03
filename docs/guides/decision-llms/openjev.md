# OpenJev

OpenJev, self-hosted: DiffusionGemma behind vLLM, or MLX on Apple silicon. It is
[razorback16/openjev](https://github.com/razorback16/openjev), Apache-2.0, read at `main` on
2026-09-22 (its README, `openjev/{config,api,engine,chat,__main__}.py`, `tests/test_api.py`) and
pinned to the docker image `razorback16/openjev:0.3.0`. A decision LLM has no `upstream_api.py`,
so every name quackd spells lives in [`catalogue.py`](../../quackd/agent/decision/catalogue.py).

**Nothing here has ever answered a real robot.**

```bash
docker run -d --gpus all --ipc=host -p 127.0.0.1:8080:8080 -v ~/.cache/huggingface:/root/.cache/huggingface razorback16/openjev:0.3.0   # not run here: the vLLM path, 24 GB of VRAM and an 18 GB download
pip install -e ".[mlx]" && OPENJEV_BACKEND=mlx python -m openjev   # not run here: the MLX path, from a checkout
uv pip install "quackd[decision]"   # not run here
quackd run arm-grip-check --robot lerobot:mock --decision-llm openjev --decision-mode shadow   # not run here: no decision LLM is installed on this machine
```

## The row

| Field | Value |
|---|---|
| `name` | `openjev` |
| `backend` | `SYSTEM_ONE`, which is `systemone`: `POST /v1/systemone` over `typesafe_sdk`, the client every server row here shares |
| `summary` | `OpenJev, self-hosted: DiffusionGemma behind vLLM, or MLX on Apple silicon` |
| `install` | `docker run -d --gpus all --ipc=host -p 127.0.0.1:8080:8080 -v ~/.cache/huggingface:/root/.cache/huggingface razorback16/openjev:0.3.0`, its own README's command verbatim and nothing else, so the cell is one thing you can paste. The Apple silicon path is `OPENJEV_BACKEND=mlx python -m openjev` instead, in the quickstart above |
| `url` | `http://127.0.0.1:8080` |
| `key_env` | `none`, a server that wants no key: the client sends the literal `local` instead, the word `NO_KEY` holds in `systemone.py`, so a hosted key sitting in the same `.env` is never posted to a port on your own machine |
| `model` | `openjev-latest` |
| `price` | `none`, which is not free and not unknown: it is a server you run, and `factory.resolve_decision_price` turns it into the self-hosted rate quackd already prices a local model at |
| `extra` | `decision`, installed as `quackd[decision]` |
| `sdk` | `typesafe_sdk`, the import that says whether that extra is here |

That is `PRESETS["openjev"]` in [`catalogue.py`](../../quackd/agent/decision/catalogue.py); a test
reads the url, the model id, the key variable, the install line and the extra back off this page;
`--decision-url` overrides the address and `--decision-llm openjev:openjev-0.1` the model id.

## What it is

DiffusionGemma 26B-A4B, 26 billion parameters with about 4 billion active, read in a single
forward pass: a discrete-diffusion model rather than an autoregressive one, which is where the
speed comes from. vLLM serves `nvidia/diffusiongemma-26B-A4B-it-NVFP4` on NVIDIA; MLX serves
`mlx-community/diffusiongemma-26B-A4B-it-4bit` on Apple silicon. The NVIDIA path wants at least 24
GB of VRAM (its README, tested on an RTX PRO 6000 Blackwell) plus 18 GB of weights into
`~/.cache/huggingface` on first start; MLX wants about 16 GB free. Its own benchmark table puts
one request at a p50 of 94 ms and 64 concurrent at a p95 of 1109 ms, past quackd's one-second
budget. Apache-2.0, weights included (NVIDIA / Google); its interpreter floor is its own.

## What to know before you point a robot at it

- **Its accepted model ids are a closed set** -- `openjev-latest`, `openjev-0.1`, `jev-latest`,
  `jev-preview`. Anything else, a pinned `jev-1.13.0` included, is an HTTP 400 `Unknown model`, as
  its own README warns. That is why every row carries its own model id: a shared default would
  have been wrong here on every single request, and invisible until a server was listening to say
  so.
- **This server computes confidence by its own formula**, `1 - H(p)/ln K`, while quackd's floors
  -- brake 0.50, read 0.60, motion 0.85, confirm 0.90 -- are two numbers TypeSafe publish with
  two of quackd's own between them, all four shaped around Jev and never measured against this
  one. See [How a turn is
  decided](../decision-llms.md#how-a-turn-is-decided).

> [!WARNING]
> Both defaults are loopback (`OPENJEV_HOST` is `127.0.0.1`; the image binds `0.0.0.0` inside the
> container but publishes `127.0.0.1:8080:8080`). Move either off it and you have put an
> unauthenticated inference server on a network: there is no key unless `OPENJEV_API_KEY` is set.

## VERIFIED (read from its README and source on 2026-09-22, at `razorback16/openjev:0.3.0`)

| Thing | Value | What quackd does with it |
|---|---|---|
| Address and auth | `OPENJEV_HOST` 127.0.0.1 and `OPENJEV_PORT` 8080 (`openjev/__main__.py`); the image binds 0.0.0.0 inside the container but publishes `127.0.0.1:8080:8080`, so it is reachable from the host either way. No auth unless `OPENJEV_API_KEY` is set, then `Authorization: Bearer`, with an optional second gate, `X-Origin-Secret`, when `OPENJEV_ORIGIN_SECRET` is; `GET /health` is behind neither, because the middleware guards only `/v1/` | the row's `url` is `http://127.0.0.1:8080` and `--decision-url` is how you say otherwise; the row has no `key_env`, so `systemone.py` sends `NO_KEY`, the literal word `local`, and neither header |
| Model ids | `MODEL_ALIASES` is exactly `openjev-latest`, `openjev-0.1`, `jev-latest`, `jev-preview`; anything else is 400 `{"detail": {"error_type": "api_usage_error", "message": "Unknown model: <name>"}}`. The response `model` is always the literal `openjev-0.1` (`MODEL_VERSION`) whichever alias you sent, and `GET /v1/models` lists only `openjev-latest`, `openjev-0.1` and `diffusiongemma-26b` -- not the `jev-*` aliases, which do work -- in a `{"models": [...]}` shape rather than OpenAI's `{"object":"list","data":[...]}` | the row pins `openjev-latest` rather than inheriting Jev's own pinned id, which would be a 400 on every single request; nothing reads `/v1/models`, so a record naming `openjev-latest` and an answer naming `openjev-0.1` are the same turn |
| Caps | `MAX_LABEL_IDS = 128` in `openjev/engine.py`, commented "vLLM logprob_token_ids cap per request" and asserted by its tests; over it is 400 `Too many choices. Must have at most 128 choices.` Score levels cap at 10, and a single-option choice or single-level score is answered locally at probability 1.0 and bills no tokens (`engine.build_schema` puts them in `forced`) | theoretical here: `MAX_CALLS_PER_VERB` is 12 in `stepper.py`, so quackd's widest fan-out is far under 128, and `escalate` rides every Choice, so the smallest one quackd sends has two options and is never `forced` |
| Confidence, noise and the seed | confidence is `1 - H(p)/ln K`; a slot whose entropy exceeds `OPENJEV_AUTO_THRESHOLD` (0.1) is re-read with fresh noise up to `OPENJEV_AUTO_MAX` (4) times and averaged, which multiplies `input_tokens` -- one of its tests asserts `input_tokens == 4 * 123` -- and the seed is sha256 over the state, the questions and any image URLs | the confidence is compared against `FLOORS` in `stepper.py`, two of them numbers TypeSafe publish and two of them quackd's own and not this server's; a cost figure from here is not a per-request constant; and identical turns get identical answers, which is what makes a shadow-mode bench replayable |
| `usage` | both fields present, `output_tokens` 0 unless the OpenJev-only `think` field was set, "as in Jev's contract unless a thought was generated" | `Stepper._bill` uses a measured count where there is one and marks the turn `usage_estimated` where there is not; a self-hosted row costs $0 either way |
| Concurrency, and what it publishes | the vLLM backend takes 64 reads in flight (`OPENJEV_MAX_INFLIGHT`) behind a queue of 512, then 529 `overloaded_error` with `retry-after: 1`; the MLX backend serves one at a time; and an undocumented 503 `{"error_type": "api_error", "message": "inference backend unavailable"}` with `retry-after: 2` says it cannot reach vLLM, which Jev's documented set (422/400/401/403/429/529) has no room for. Its README's table, an RTX PRO 6000 at 38% of the GPU with 3 questions a request: 10.7 req/s at p50 94 ms at 1 concurrent, 43.3 req/s at p50 367 ms at 16, and 57.4 req/s at p50 760 ms with p95 1109 ms at 64; MLX is about 0.2-0.4 s for a 3-question request on an M3 Ultra | quackd sends one request a turn and retries once (`MAX_RETRIES = 1`), all of it inside `TIMEOUT_S`; whatever the SDK raises is caught in `Stepper.advise`, recorded as `gate: error` and handed to the model, because a decision LLM may never end a run. The seam allows 1.0 s, so the MLX figure fits with room and its own p95 at 64 concurrent does not |
| Install, and the fork it rides | not on PyPI, and `pip install openjev` installs an unrelated package (PyPI `openjev` 0.0.1 points at github.com/balys/openjev); the docker command is its README's, verbatim, where `--ipc=host` is vLLM's shared memory and the mount is the 18 GB of weights. The vLLM path depends on the unmerged vllm-project/vllm#57250 and pins `razorback16/vllm` at `baa833874881ba62cef99e0c5b716fb136c4a009`, whose `vllm_xargs` fields its README calls provisional | the line a reader copies out of `doctor` is the one that cannot land on somebody else's project, and a restarted container does not download the weights again; nothing quackd sends touches `vllm_xargs`, but the fork is why this pin can move under you |
| Other routes | `POST /v1/chat/completions`, which deviates from OpenAI by silently ignoring `temperature`, `seed`, penalties and reasoning, and `GET /v1/models`; every response carries `x-typesafe-request-id` and `x-request-id`. Its README also advertises a hosted third-party endpoint, api.codiv.ai with `sk-codiv-...` keys | quackd posts `/v1/systemone` and nothing else, and this row points at a server you started, never at that hosted endpoint |

## UNVERIFIED, and what quackd does about each

| Name | The assumption | What quackd does |
|---|---|---|
| `WIRE_COMPATIBLE` | that its `/v1/systemone` response keeps the shape `typesafe_sdk` parses | the SDK requires the `model` and `usage` keys present, contents allowed to be null; quackd's own reader takes an answer as an object or as a plain mapping, and one it cannot read costs the turn as `gate: unreadable` or `gate: error` and hands it to the model rather than raising |
| `CONFIDENCE_MEANS_THE_SAME` | that its confidence is comparable to the one quackd's floors were set against. It is not: the floors (brake 0.50, read 0.60, motion 0.85, confirm 0.90) are two numbers TypeSafe publish with two of quackd's own set between them, all four Jev-shaped and none of them measured here, and this server computes confidence as `1 - H(p)/ln K` | nothing automatic. `--decision-mode shadow` records what it would have chosen without letting it act, which is how the floors get re-tuned |
| `LATENCY_UNDER_ONE_SECOND` | that it answers inside the turn budget. Its own table says yes at 1 concurrent and no at the p95 of 64, and nobody has measured it against a robot | `TIMEOUT_S` is 1.0 s, enforced with `asyncio.wait_for` around `decide()` in `stepper.py` so it holds whatever the backend is; a turn that runs out escalates to the model and the record says how long it waited |
| `USAGE_REPORTED` | whether its token counts mean anything, given that an auto re-read multiplies them | where a count is missing or not a measurement, quackd estimates the request at four characters to the token and marks the turn `usage_estimated`, and a self-hosted row is costed at $0 either way |
| `PYTHON_FLOOR` | its own interpreter floor is its own | nothing. `quackd[decision]` is a small HTTP client that runs on quackd's own Python, and the server runs in whatever environment you start it in |
| `MODEL_IDS_ARE_A_CLOSED_SET` | that the id in the row stays accepted: it is `openjev-latest` today, and a pinned Jev version is a 400 | the id lives in the row rather than in shared code, so correcting it is one line, and `--decision-llm openjev:<id>` is the override in the meantime |
| `CHOICES_CAPPED_AT_128` | that quackd's fan-out fits under `MAX_LABEL_IDS` | it does, by a wide margin: `MAX_CALLS_PER_VERB` is 12, and the widest verb quackd ships is six |
| `PIP_NAME_IS_A_TRAP` | that the PyPI name stays somebody else's | the install line never says `pip install openjev`, and this page says why where somebody copying a command would see it |

## Status

It has never answered a real robot. The client that would reach it is exercised against a stub
rather than against OpenJev: `tests/stub_decision_llm.py` stands in for a server and
`tests/test_decision_llms.py` covers the flags, the row, the unsent key and the timeout. Without
the extra, `quackd doctor` prints this row as `missing (quackd[decision])`, `none needed`,
`openjev-latest` and `http://127.0.0.1:8080`; `--decision-mode shadow` is how that changes.

## How to help

Run it in shadow mode on your own bench ([Measuring it
yourself](../decision-llms.md#measuring-it-yourself)) and report the agreement rate, the latency
mean and max, the answers that surprised you, and which interface and port you served on, vLLM or
MLX. A confidence floor moves on a calibration curve of your own and nothing else: [How a turn is
decided](../decision-llms.md#how-a-turn-is-decided) says what to plot, and
[CONTRIBUTING.md](../../CONTRIBUTING.md) says where to send it.
