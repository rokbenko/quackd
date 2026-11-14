# Kev

Kev, self-hosted: Qwen3.5 with a decision head, on your own GPU. It is
[jaredpalmer/kev](https://github.com/jaredpalmer/kev), Apache-2.0, read on 2026-11-14 at its
`kev-1.0` tag (`6b719c3c`), the Kev 1.0 release of 2026-10-01, "the first versioned release of the
whole Kev family". The row pins Kev 1.0 twice, as this page read it: `git clone --branch kev-1.0`
for the server, and `--run jaredpalmer/kev-4b@v1.0` for the weights, the `v1.0` tag Kev 1.0 put on
each of its four checkpoints on Hugging Face. Kev's own release notes pin the weights alone and
clone `main`, which was 23 commits further on at this read, with fixes this pin leaves out, among
them an explicit `--device`, a startup probe that falls back from CUDA to MPS to the CPU, and CUDA
graphs kept to NVIDIA rather than also on AMD's ROCm; none of them changes the routes, the key, the
limits or the answers this page records. A decision LLM has no `upstream_api.py` the way an adapter
does, so every name quackd spells for it lives in its row in
[`catalogue.py`](../../../quackd/agent/decision/catalogue.py).

**Nothing here has ever answered a real robot.**

```bash
git clone --branch kev-1.0 https://github.com/jaredpalmer/kev   # not run here
cd kev                                                          # not run here
uv sync --extra serve                                           # not run here: wants uv, and its own floor is Python 3.12
uv run --extra serve python -m kev.serve --run jaredpalmer/kev-4b@v1.0 --port 8009   # not run here: pulls the weights from Hugging Face the first time

quackd run arm-grip-check --robot lerobot:mock --decision-llm kev --decision-mode shadow   # not run here: no decision LLM is installed on this machine
```

## The row

| Field | Value |
|---|---|
| `name` | `kev` |
| `backend` | `systemone`, so `SystemOneLLM` over `typesafe_sdk`, and the same `POST /v1/systemone` every other server here speaks |
| `summary` | `Kev, self-hosted: Qwen3.5 with a decision head, on your own GPU` |
| `install` | `git clone --branch kev-1.0 https://github.com/jaredpalmer/kev && cd kev && uv sync --extra serve && uv run --extra serve python -m kev.serve --run jaredpalmer/kev-4b@v1.0 --port 8009` |
| `url` | `http://127.0.0.1:8009` |
| `key_env` | `none`, a server that wants no key: `systemone.py` sends the literal `local` instead, so a `TYPESAFE_API_KEY` sitting in the same `.env` is never posted to a port on your own machine |
| `llm_id` | `kev-latest` |
| `price` | `none`, which is neither free nor unknown: it is a server you run, and `factory.resolve_decision_price` turns it into the self-hosted rate quackd already charges a local LLM at |
| `extra` | `decision`, installed with `quackd[decision]` |
| `sdk` | `typesafe_sdk`, the import that says whether that extra is here |

That is `PRESETS["kev"]` in `catalogue.py`. A test checks that this page carries the row's address,
LLM id, install line and extra, so a row that changes without its page fails the suite.
`--decision-url http://127.0.0.1:8008` overrides the address, `--decision-llm kev:<id>` the LLM id,
which Kev only echoes back.

## What it is

Kev 1.0 is four checkpoints. Kev-0.8B, 4B and 9B are Qwen3.5 base weights, frozen, with a rank-16
LoRA adapter and a small pointer head on top; Kev-27B fine-tunes every weight of Qwen's post-trained
Qwen3.8-27B, 51 GB that are on Hugging Face only. The checkpoint is fixed **at startup** by `--run`:
`jaredpalmer/kev-4b@v1.0` in the row, the same with `-0.8b`, `-9b` or `-27b`, a local checkpoint
directory, or another Hub revision such as `jaredpalmer/kev-4b@qwen3`. The weights download from
Hugging Face when the server starts, before it listens, so the first start is slow and needs
network. Its README puts Kev-4B on an L40S or an H100 and Kev-27B on an 80 GB GPU, runs them through
MLX on Apple silicon, measured for the two smaller ones and "expected, not measured" for 9B and 27B,
and wants Python 3.12 or 3.13; its `pyproject.toml` pins `transformers>=5.17,<6` and
`torch>=2.6,<2.9`, and the server runs bf16 on a GPU and fp32 on a CPU unless `KEV_DTYPE` says
otherwise. It times Kev-4B at 18.1 ms for six questions on an H100 and 721 ms for five on an Apple
M5, 136 ms when the text repeats, all inside quackd's one-second budget before any HTTP is added.
Apache-2.0.

## What to know before you point a robot at it

- **`pip install kev` installs something else entirely.** That name on PyPI is an unrelated
  project at 0.10.0 by Brian Jinwright, "K.E.V. (Keys, Extra Stuff, and Values) is a Python ORM for
  key-value stores and document databases based on Valley". `jaredpalmer/kev` is installed from a
  clone and ships no console script, hence `python -m kev.serve`.
- **The model field is inert.** `kev/api.py` declares `model: str = "kev-latest"`, an
  unvalidated string with a default, and `serve.py` puts whatever you sent back in the answer:
  its own tests post `jev-latest` and assert the echo. `MODEL_NAMES`, `kev-latest` and
  `jev-latest`, is only what `GET /v1/models` lists, and `kev-4b` or `kev-9b` in the field does
  **not** switch checkpoints.
- **Its code default port is 8008 and every README example passes `--port 8009`.** quackd's row
  says 8009, the documented command being the one people run, and this is exactly the class of
  detail that makes a per-row address better than a shared default. `--decision-url` is how you
  say otherwise.
- **It batches on a CUDA GPU.** One thread runs every forward pass and takes up to 64 queued
  requests at once (`MAX_BATCH`), and runs them "with CUDA graphs, shared state and row passes;
  otherwise one request at a time", the graphs being on for any GPU torch reports as `cuda`, AMD's
  ROCm included at this tag; its README has a container serving Kev-4B at about 101 requests a
  second on an H100 for 64 clients, network time extra. `latency_ms` times the batch a request ran
  in, "not its wait in the queue", so a busy instance adds wait that the field does not show.
- **A long state is refused, not cut.** A state over 65,536 tokens is a 422 unless the server was
  started with `KEV_TRUNCATE_STATES=1`, which reads its first 65,536 tokens and marks the response
  `truncated`, and a question row over 8,192 more is a 422 either way. Its README validates
  Kev-0.8B, 4B and 9B to 8,192 tokens: "at 32k all three are measurably less accurate than at 8k".
- **Its confidence is TypeSafe's reference arithmetic, not a measurement.** A choice scores
  `(p_max - 1/K) / (1 - 1/K)`, exactly `1.0` at K=1; a score scores
  `max(0, 1 - E|level - mode| / D)`, D being the mean absolute deviation of a uniform
  distribution over its L levels. `api.py` says both "mirror TypeSafe's reference adapter,
  system-one-adapter 0.2.1". quackd's floors are two of TypeSafe's numbers with two of its own
  between them, all four shaped around Jev and none of them around this ([how a turn is
  decided](README.md#how-a-turn-is-decided)).

> [!WARNING]
> Kev **authenticates nothing unless it is told to**. It binds `127.0.0.1` unless it is started
> with `--host 0.0.0.0`, and it is open unless `KEV_API_KEY` is set, which makes every path under
> `/v1` want `Authorization: Bearer <key>` and answer anything else with a 401; CORS allows every
> origin either way. quackd sends this row the literal `local` as its key and has no way to send
> another, so a Kev started with `KEV_API_KEY` refuses quackd, and one started with
> `--host 0.0.0.0` and no key leaves nothing between a network and an unauthenticated LLM with a
> robot downstream of it.

## VERIFIED (read from its README and source on 2026-11-14, at the `kev-1.0` tag `6b719c3c`, the weights at `v1.0`)

| Thing | Value | What quackd does with it |
|---|---|---|
| Weights and checkpoint | Kev 1.0: Qwen3.5 0.8B, 4B or 9B frozen, plus a rank-16 LoRA adapter and a small pointer head, and Kev-27B with every weight of Qwen3.8-27B trained; which one is fixed at startup by `--run`, a Hub revision after an `@`, and "Each Hub repo has a `v1.0` tag, so `--run jaredpalmer/kev-4b@v1.0` always loads the same weights", which for `kev-4b` is the commit `6cfce5c2` of 2026-10-01; they download from Hugging Face on first use | nothing is imported or vendored: quackd holds an address and an LLM id, and the row's install line serves `jaredpalmer/kev-4b@v1.0` |
| Install and the name | clone only: its README installs it from a clone, `pip install kev` installs the unrelated K.E.V. ORM at 0.10.0, and it ships no console script, hence `python -m kev.serve`. Python `>=3.12,<3.14`, `transformers>=5.17,<6`, `torch>=2.6,<2.9`, and `mlx-lm` in its `serve` extra on Apple silicon | the row's install line clones the `kev-1.0` tag and pins the weights to `v1.0`, where Kev 1.0's release notes clone `main`, and the hub's table quotes it verbatim; the pins are the server's own business, `quackd[decision]` being an HTTP client on quackd's Python |
| Bind and port | `--host` defaults to `127.0.0.1` and `--port` to `8008` in `serve.py`'s `main()`, `0.0.0.0` being how it serves "beyond this machine", while every README command passes `--port 8009` | the row's `url` is `http://127.0.0.1:8009`, spelled `127.0.0.1` rather than `localhost` because a `localhost` resolving to `::1` first is refused with nothing useful said; `--decision-url http://127.0.0.1:8008` is how a reader who ran it bare says otherwise |
| Auth and CORS | open while `KEV_API_KEY` is unset; once it is set, every path under `/v1` wants `Authorization: Bearer <key>` and anything else is a 401, "missing or invalid API key"; `allow_origins=["*"]`, all methods, all headers, either way | `systemone.py` sends `NO_KEY`, the literal `local`, which is what its own README and release notes do (`api_key="local"`), and has no way to send another, so a keyed Kev refuses quackd |
| Routes | `POST /v1/systemone`, `/v1/systemone/permute`, `/v1/systemone/separate`, `GET /v1/models`; no `/health`, and the listing is `{"models": [...]}` rather than OpenAI's | quackd sends the first and reads neither of the others; `quackd doctor` reports the row, never the server |
| Model field and response | `model: str = "kev-latest"` at `api.py`'s request model, echoed back at `serve.py`'s handler, with `MODEL_NAMES` (`kev-latest`, `jev-latest`) only what `/v1/models` lists; the response is `model`, `answers`, `usage`, `latency_ms`, and `truncated` as well from a server started with `KEV_TRUNCATE_STATES=1` | the record says what quackd asked for, which here is all the field can mean; `_answer` in `stepper.py` reads the mapping's `answers` directly, and there is no `id` or `created` to ignore |
| Noul answers | `{"type": "noul", "noul": <p_true>}`, `api.py`'s noul answer: no confidence and no probabilities | `done` and `need_human` read the raw probability against 0.5, the one place quackd needs no confidence; false is `1 - noul`, as its own `RemotePredictor` derives it |
| Confidence and rounding | choice is `(p_max - 1/K) / (1 - 1/K)` and `1.0` at K=1, score is `max(0, 1 - E\|level - mode\| / D)` with D the mean absolute deviation of a uniform distribution over the L levels, both on p normalised to sum 1 and, by `api.py`, mirroring "TypeSafe's reference adapter, system-one-adapter 0.2.1"; every probability goes through `round_prob()`, four decimal places, which keeps a rounded distribution inside TypeSafe's `\|sum - 1\| < 0.02` at the 255-option maximum | compared with `FLOORS`: brake 0.50, read 0.60, motion 0.85, confirm 0.90. A probability map often does not sum to 1.0, and its own tests allow `abs_tol=0.03`, so quackd records the spread rather than re-normalising it |
| Usage and `latency_ms` | usage is `{input_tokens, output_tokens}`, no `total_tokens`, with `state_tokens` and `state_tokens_used` added by a server started with `KEV_TRUNCATE_STATES=1`; the output count is the token length of the serialised answers, which `api.py` calls a "Billing-style figure"; `latency_ms` times the batch the request ran in, "not its wait in the queue" | `_usage` reads `input_tokens`, so a turn is billed measured rather than estimated, and output is multiplied by a rate of 0; quackd times the whole call into `latency_s`, which reads higher and is the figure the timeout judges |
| Limits and truncation | `SERVE_MAX_STATE` 65,536 and `SERVE_MAX_BRANCH` 8,192 more, in `model.py`; a choice takes 1 to 255 options and a score 1 to 255 levels (`MAX_OPTIONS` in `api.py`); a longer state is a 422 unless the server was started with `KEV_TRUNCATE_STATES=1`, which reads its first 65,536 tokens and marks the response `truncated`, and a longer question row is a 422 either way. Kev-0.8B, 4B and 9B trained on states of at most 7,552 tokens, mostly of up to 384, and its README validates them to 8,192 | quackd caps its state at 6,000 characters soft and 24,000 hard, about 6,000 tokens at most at four characters a token, far inside both limits and inside the 8,192 its README validates; its largest Choice on any shipped body is 28 options, every core and body verb that can be one plus `escalate`; a 422 is caught like anything else the SDK raises, recorded as `gate: error` and handed to the pilot LLM |
| Concurrency and latency | one thread runs every forward pass and takes up to `MAX_BATCH`, 64, queued requests at once, together on a CUDA GPU and one at a time otherwise; its README: Kev-4B answers six questions about a new short text in 18.1 ms on an H100 and 41.5 ms on an L40S, a container serves it at about 101 requests a second on an H100 for 64 clients, network time extra, and 721 ms for five questions on an Apple M5, 136 ms when the text repeats | one request a turn; `TIMEOUT_S` is 1.0 s, which every one of those clears before HTTP is added |
| Calibration and cache | each checkpoint ships a fitted temperature, 2.41 for Kev-4B, applied at load, and `KEV_TEMPERATURE=1.0` returns raw probabilities instead, which never changes the argmax; a state-prefix cache keeps four states (`KEV_PREFIX_CACHE`) of up to 65,536 tokens in all (`KEV_PREFIX_MAX_TOKENS`), so a repeated state pays only for its question rows | a floor is a statement about calibrated probabilities, so leave the temperature alone; quackd's state changes every turn, so assume the uncached number |

## UNVERIFIED, and what quackd does about each

| Name | The assumption | What quackd does |
|---|---|---|
| `WIRE_COMPATIBLE` | that its response keeps the shape `typesafe_sdk` parses | the SDK wants the `model` and `usage` keys present and Kev always sends both; quackd's own reader takes an answer as an object or as a plain mapping, and one it cannot read costs the turn as `gate: unreadable` or `gate: error` and hands it to the pilot LLM rather than raising |
| `CONFIDENCE_MEANS_THE_SAME` | that its confidence is comparable to the one quackd's floors were set against. It is not: the floors (brake 0.50, read 0.60, motion 0.85, confirm 0.90) are two numbers TypeSafe publish with two of quackd's own set between them, all four Jev-shaped and none of them measured here, and this server computes confidence by the formula above | nothing automatic. `--decision-mode shadow` records what it would have chosen without letting it act, which is how the floors get re-tuned |
| `LATENCY_UNDER_ONE_SECOND` | that it answers inside the turn budget. Its own figures put Kev-4B at 721 ms for five questions through MLX on a Mac, inside it with little to spare once HTTP is added, and at 18.1 ms on an H100. Nobody has measured any checkpoint against a robot | `TIMEOUT_S` is 1.0 s, enforced with `asyncio.timeout` around `decide()` in `stepper.py` so it holds whatever the backend is; a turn that runs out escalates to the pilot LLM and the record says how long it waited |
| `USAGE_REPORTED` | whether its token counts mean anything, given that the output count is the answer it serialised rather than anything it generated | where a count is missing or is not a measurement, quackd estimates the request at four characters to the token and marks the turn `usage_estimated`; a self-hosted row is costed at $0 either way |
| `PYTHON_FLOOR` | that its 3.12 floor is somebody else's problem | nothing. `quackd[decision]` is a small HTTP client that runs on quackd's own Python, and the server runs in whatever environment you started it in |
| `SHARED_INSTANCE_IN_TIME` | that robots sharing one instance are each still answered inside the turn budget. It batches on a CUDA GPU and answers one request at a time elsewhere, and its README measures throughput, not a flock's turns | `TIMEOUT_S` holds per request whatever the batch, and a turn that runs out escalates to the pilot LLM, so a crowded instance costs pilot calls and never a stuck run |
| `STATE_FITS` | that the state quackd sends is never one the server refuses | quackd caps its own state at 6,000 characters soft and 24,000 hard and records `state_chars` and what it trimmed, far inside the 65,536 tokens a 422 starts at |
| `PIP_NAME_IS_A_TRAP` | that a reader installs the right thing | the row's install line clones rather than pip-installs, for exactly this reason |

## Status

Kev has never answered a real robot, and this page says so until somebody has. The client that
would reach it is exercised against a stand-in, `tests/fake_systemone.py`, installed in place of the
`typesafe_sdk` it speaks through (`tests/test_decision_llms.py`), so no request crosses a wire: that
proves quackd reads an answer, a missing usage count and a timeout as described here, and nothing
about the server. Without the extra, `quackd doctor` prints this row as missing `quackd[decision]`,
no key needed, `kev-latest`, `http://127.0.0.1:8009`, and `--decision-mode shadow` is how that
status changes.

## How to help

Run it in shadow mode on your own bench ([measuring it
yourself](README.md#measuring-it-yourself)) and report the agreement rate, the latency
against a real robot's state rather than a benchmark one, and the answers that surprised you,
which is the only one of the three a table cannot produce. Say which checkpoint `--run` loaded and
which interface and port you served it on, since 8008 and 8009 both exist here and `--host
0.0.0.0` or a reverse proxy changes what the warning above is worth. A confidence floor moves on a
calibration plot and nothing else ([how a turn is decided](README.md#how-a-turn-is-decided)), and
[CONTRIBUTING.md](../../../CONTRIBUTING.md) says where such a change goes.
