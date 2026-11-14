# OpenJev

OpenJev, self-hosted: DiffusionGemma behind vLLM, or MLX on Apple silicon. It is
[razorback16/openjev](https://github.com/razorback16/openjev), Apache-2.0, read on 2026-11-14 at
its `Release 0.7.0` commit (`b4b284ac`, 2026-10-12: its README, `docker/Dockerfile`,
`openjev/{config,api,engine,chat,__main__}.py`, `tests/test_api.py`) and pinned to the docker
image `razorback16/openjev:0.7.0`, the newest tag of that image, pushed minutes after that commit.
GitHub carries no tag for it, and the 0.7.1 and 0.7.2 that `main` has released since are images of
other backends. Never `:latest`, which Docker Hub still points at 0.2.0. A decision LLM has no
`upstream_api.py`, so every name quackd spells lives in
[`catalogue.py`](../../../quackd/agent/decision/catalogue.py).

**Nothing here has ever answered a real robot.**

```bash
docker run -d --gpus all --ipc=host -p 127.0.0.1:8080:8080 -v ~/.cache/huggingface:/root/.cache/huggingface razorback16/openjev:0.7.0   # not run here: the vLLM path, 24 GB of VRAM and an 18 GB download
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
| `install` | `docker run -d --gpus all --ipc=host -p 127.0.0.1:8080:8080 -v ~/.cache/huggingface:/root/.cache/huggingface razorback16/openjev:0.7.0`, its own README's command verbatim and nothing else, so the cell is one thing you can paste. The Apple silicon path is `OPENJEV_BACKEND=mlx python -m openjev` instead, in the quickstart above |
| `url` | `http://127.0.0.1:8080` |
| `key_env` | `none`, a server that wants no key: the client sends the literal `local` instead, the word `NO_KEY` holds in `base.py`, so a hosted key sitting in the same `.env` is never posted to a port on your own machine |
| `llm_id` | `openjev-latest` |
| `price` | `none`, which is not free and not unknown: it is a server you run, and `factory.resolve_decision_price` turns it into the self-hosted rate quackd already prices a local LLM at |
| `extra` | `decision`, installed as `quackd[decision]` |
| `sdk` | `typesafe_sdk`, the import that says whether that extra is here |

That is `PRESETS["openjev"]` in [`catalogue.py`](../../../quackd/agent/decision/catalogue.py); a
test checks that this page carries the row's url, LLM id, install line and extra, so a row that
changes without its page fails the suite; `--decision-url` overrides the address and
`--decision-llm openjev:openjev-0.1` the LLM id.

## What it is

DiffusionGemma 26B-A4B, 26 billion parameters with about 4 billion active, read as a diffusion
canvas: a discrete-diffusion LLM rather than an autoregressive one, each read a single forward
pass, which is where the speed comes from. vLLM serves `nvidia/diffusiongemma-26B-A4B-it-NVFP4`
on NVIDIA; MLX serves `mlx-community/diffusiongemma-26B-A4B-it-4bit` on Apple silicon. The NVIDIA
path wants a GPU with 24 GB or more (its README's own runs are on an RTX PRO 6000 Blackwell) plus
about 18 GB of weights into `~/.cache/huggingface` on first start; MLX wants about 16 GB free and
reads one request at a time, "for local use, not for serving". Its own benchmark, run on 0.6.0
with three questions a request and no prefill reused, puts a 49-token state at a p50 of 30 ms one
at a time and a 2,047-token one at 83 ms, and 64 at a time at a p95 of 669 ms and 5.4 s, the second
far past quackd's one-second budget. Apache-2.0, and so are the weights (NVIDIA / Google); it wants
Python 3.10 or later.

## What to know before you point a robot at it

- **Its accepted LLM ids are a closed set** -- `openjev-latest`, `openjev-0.1`, `jev-latest`,
  `jev-preview`. Anything else, a pinned `jev-1.14.0` included, is an HTTP 400 `Unknown model`,
  as its own README warns of `jev-1.13.0`, unless `OPENJEV_MODEL_ROUTES` sends that name to
  another container. That is why every row carries its own LLM id: a shared default would have
  been wrong here on every single request, and invisible until a server was listening to say so.
- **This server computes confidence by its own formula**, `1 - H(p)/ln K`, while quackd's floors
  -- brake 0.50, read 0.60, motion 0.85, confirm 0.90 -- are two numbers TypeSafe publish with
  two of quackd's own between them, all four shaped around Jev and never measured against this
  one. See [How a turn is decided](README.md#how-a-turn-is-decided).
- **An uncertain answer is read again, unbilled.** Its README: "If a slot is uncertain (entropy >
  0.1), OpenJev reads three more times with fresh noise and averages the four results", and
  "These extra reads add no tokens to `usage`", so `usage` counts one read whatever the server
  did. The noise is seeded from the request, so the same turn gets the same answer.
- **It speaks a second wire format.** Since 0.7.0 it also answers OpenAI's Decisions API at
  `POST /v1/decisions`, "so the OpenAI SDKs' `client.decisions.create` works too". This row
  speaks `/v1/systemone`, as every server row here does.

> [!WARNING]
> Both defaults are loopback: `OPENJEV_HOST` is `127.0.0.1`, and the image sets
> `OPENJEV_HOST=0.0.0.0` inside the container but its README's command publishes
> `127.0.0.1:8080:8080`. Move either off it and you have put an unauthenticated inference server on
> a network: there is no key unless `OPENJEV_API_KEY` is set, and quackd sends this row the literal
> `local`, so an OpenJev started with `OPENJEV_API_KEY` refuses quackd.

## VERIFIED (read from its README and source on 2026-11-14, at its `Release 0.7.0` commit, `b4b284ac`)

| Thing | Value | What quackd does with it |
|---|---|---|
| Address and auth | `OPENJEV_HOST` 127.0.0.1 and `OPENJEV_PORT` 8080 (`openjev/__main__.py`); `docker/Dockerfile` sets `OPENJEV_HOST=0.0.0.0` inside the container, and the README's command publishes `127.0.0.1:8080:8080`, so it is reachable from the host either way. No auth unless `OPENJEV_API_KEY` is set, then `Authorization: Bearer`, with an optional second gate, `X-Origin-Secret`, when `OPENJEV_ORIGIN_SECRET` is; `GET /health` is behind neither, because the middleware guards only `/v1/` | the row's `url` is `http://127.0.0.1:8080` and `--decision-url` is how you say otherwise; the row has no `key_env`, so `systemone.py` hands the SDK `NO_KEY`, the literal word `local`, which it sends as `Authorization: Bearer local`: a keyed OpenJev answers that with a 401, and no `X-Origin-Secret` is ever sent |
| LLM ids | `MODEL_ALIASES` is `openjev-latest` and `openjev-0.1`, and `SDK_ALIASES` is `jev-latest` and `jev-preview`, "accepted so TypeSafe's SDKs work unchanged"; anything else is 400 `{"detail": {"error_type": "api_usage_error", "message": "Unknown model: <name>"}}`, unless `OPENJEV_MODEL_ROUTES` forwards that name to another container. The response `model` is always the literal `openjev-0.1` (`MODEL_VERSION`) whichever alias you sent, and `GET /v1/models` lists `openjev-latest`, `openjev-0.1` and `diffusiongemma-26b`, and any name `OPENJEV_MODEL_ROUTES` adds -- not the `jev-*` aliases, which do work -- in a `{"models": [...]}` shape rather than OpenAI's `{"object":"list","data":[...]}` | the row pins `openjev-latest` rather than inheriting Jev's own pinned id, which would be a 400 on every single request; nothing reads `/v1/models`, so a record naming `openjev-latest` and an answer naming `openjev-0.1` are the same turn |
| Caps | a choice takes as many options as the tokenizer has one-token labels, at most `MAX_CHOICES` 255, "Jev's limit on one choice's options", and more is 400 `Too many choices. Must have at most <n> choices.`; a score takes at most 10 levels; one read takes at most `MAX_LABEL_IDS` 512 label tokens, "the image raises it from 128"; a request takes at most 256 questions (`OPENJEV_MAX_QUESTIONS`), 8 images and a 64 MiB body; and a single-option choice or single-level score is answered locally at probability 1.0 and bills no tokens (`engine.build_schema` puts them in `forced`) | theoretical here: quackd's largest Choice on any shipped body is 28 options, every core and body verb that can be one plus `escalate`, far under every one of these; and every Choice quackd sends has at least two options, `next_verb` with `escalate` and `feasible` with its three answers, so none is `forced` |
| Confidence, noise and the seed | confidence is `1 - H(p)/ln K`, clamped to 0..1; a request with a slot whose entropy exceeds `OPENJEV_AUTO_THRESHOLD` (0.1) is read again with fresh noise, up to `OPENJEV_AUTO_MAX` (4) reads in all, and averaged, and "re-reads are the server's own policy and are not billed", while `samples`, a field quackd never sends, bills every read it asks for; the seed is sha256 over the state, the questions and any image URLs, "Same request, same noise draws: answers are reproducible." | the confidence is compared against `FLOORS` in `stepper.py`, two of them numbers TypeSafe publish and two of them quackd's own and not this server's; identical turns get identical answers, which is what makes a shadow-mode bench replayable |
| `usage` | both fields present, `output_tokens` 0 unless the OpenJev-only `think` field was set, "as in Jev's contract unless a thought was generated" | `Stepper._bill` uses a measured count where there is one and marks the turn `usage_estimated` where there is not; a self-hosted row costs $0 either way |
| Concurrency, and what it publishes | the vLLM backend takes 64 reads in flight (`OPENJEV_MAX_INFLIGHT`) behind a queue of 512 (`OPENJEV_MAX_QUEUE`), then 529 `overloaded_error` with `retry-after: 1`; the MLX backend reads one at a time; a 503 `api_error`, "inference backend unavailable" and the exception's type, with `retry-after: 2`, says it cannot reach vLLM, and its README says a read that waits more than 120 s for vLLM gets one too, which Jev's documented set (422/400/401/403/429/529) has no room for. Its README's table, on 0.6.0 and an RTX PRO 6000 Blackwell with three questions a request and no prefill reused: a 49-token state at p50 30 ms one at a time and 331 / 669 ms p50 / p95 at 64, a 2,047-token one at 83 ms and 2.9 / 5.4 s; MLX is about 0.2-0.4 s for a 3-question request on an M3 Ultra | quackd sends one request a turn and allows the SDK one retry (`MAX_RETRIES = 1`) inside `TIMEOUT_S`, which the SDK skips when the server's `retry-after` would outlast that budget, as both of these do; whatever the SDK raises is caught in `Stepper.advise`, recorded as `gate: error` and handed to the pilot LLM, because a decision LLM may never end a run. The seam allows 1.0 s, which its table meets for a 49-token state at every concurrency it measured and for a 2,047-token one only one at a time |
| Install, and the vLLM it rides | not on PyPI, and `pip install openjev` installs an unrelated package (PyPI `openjev` 0.0.1 points at github.com/balys/openjev); the docker command is its README's, verbatim, where `--ipc=host` is vLLM's shared memory and the mount is the 18 GB of weights. The vLLM part, vllm-project/vllm#57250, was "merged on 2026-09-22", and the image "pins upstream vLLM main at `a3e0243b`" | the line a reader copies off this page is the one that cannot land on somebody else's project, and a restarted container does not download the weights again; nothing quackd sends touches `vllm_xargs` |
| Other routes | `POST /v1/decisions`, OpenAI's Decisions API over the same read, new in 0.7.0; `POST /v1/chat/completions`, which drops `temperature`, `seed`, penalties, reasoning switches and `response_format` before vLLM sees them; `GET /v1/models` and `GET /health`; every response carries `x-typesafe-request-id` and `x-request-id`. Its README also advertises a hosted endpoint, api.codiv.ai, the homepage its `pyproject.toml` names, with 100M input tokens free on sign-up and `sk-codiv-...` keys | quackd posts `/v1/systemone` and nothing else, and this row points at a server you started, never at that hosted endpoint |

## UNVERIFIED, and what quackd does about each

| Name | The assumption | What quackd does |
|---|---|---|
| `WIRE_COMPATIBLE` | that its `/v1/systemone` response keeps the shape `typesafe_sdk` parses | the SDK requires the `model` and `usage` keys present, `model` a string and the usage counts allowed to be null; quackd's own reader takes an answer as an object or as a plain mapping, and one it cannot read costs the turn as `gate: unreadable` or `gate: error` and hands it to the pilot LLM rather than raising |
| `CONFIDENCE_MEANS_THE_SAME` | that its confidence is comparable to the one quackd's floors were set against. It is not: the floors (brake 0.50, read 0.60, motion 0.85, confirm 0.90) are two numbers TypeSafe publish with two of quackd's own set between them, all four Jev-shaped and none of them measured here, and this server computes confidence as `1 - H(p)/ln K` | nothing automatic. `--decision-mode shadow` records what it would have chosen without letting it act, which is how the floors get re-tuned |
| `LATENCY_UNDER_ONE_SECOND` | that it answers inside the turn budget. Its own table says yes for a 49-token state at every concurrency it measured and for a 2,047-token one only one at a time, and nobody has measured it against a robot | `TIMEOUT_S` is 1.0 s, enforced with `asyncio.timeout` around `decide()` in `stepper.py` so it holds whatever the backend is; a turn that runs out escalates to the pilot LLM and the record says how long it waited |
| `USAGE_REPORTED` | whether its token counts mean anything, given that it bills one read where it may have run four | where a count is missing or not a measurement, quackd estimates the request at four characters to the token and marks the turn `usage_estimated`, and a self-hosted row is costed at $0 either way |
| `PYTHON_FLOOR` | its own interpreter floor is its own | nothing. `quackd[decision]` is a small HTTP client that runs on quackd's own Python, and the server runs in whatever environment you start it in |
| `MODEL_IDS_ARE_A_CLOSED_SET` | that the id in the row stays accepted: it is `openjev-latest` today, and a pinned Jev version is a 400 | the id lives in the row rather than in shared code, so correcting it is one line, and `--decision-llm openjev:<id>` is the override in the meantime |
| `CHOICES_CAPPED_AT_255` | that quackd's fan-out fits under its caps | it does, by a wide margin: the largest Choice on any shipped body is 28 options, counted from the per-verb table `tests/test_decision.py` freezes |
| `PIP_NAME_IS_A_TRAP` | that the PyPI name stays somebody else's | the install line never says `pip install openjev`, and this page says why where somebody copying a command would see it |

## Status

It has never answered a real robot. The client that would reach it is exercised against a stand-in
rather than against OpenJev: `tests/fake_systemone.py` takes the place of the `typesafe_sdk` it
speaks through, so no request crosses a wire, and `tests/test_decision_llms.py` covers the flags,
the row, the unsent key and the timeout. Without the extra, `quackd doctor` prints this row as
`missing (quackd[decision])`, `none needed`, `openjev-latest` and `http://127.0.0.1:8080`;
`--decision-mode shadow` is how that changes.

## How to help

Run it in shadow mode on your own bench ([Measuring it
yourself](README.md#measuring-it-yourself)) and report the agreement rate, the latency
mean and max, the answers that surprised you, and which interface and port you served on, vLLM or
MLX. A confidence floor moves on a calibration curve of your own and nothing else: [How a turn is
decided](README.md#how-a-turn-is-decided) says what to plot, and
[CONTRIBUTING.md](../../../CONTRIBUTING.md) says where to send it.
