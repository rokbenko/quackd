# Kev

Kev, self-hosted: Qwen3.5 with a decision head, on your own GPU. It is
[jaredpalmer/kev](https://github.com/jaredpalmer/kev), Apache-2.0, read at `main` on 2026-09-22:
no tag, no release, and a `pyproject.toml` declaring `version = "0.1.0"` for something never
published, so a branch is the only pin there is. A decision LLM has no `upstream_api.py` the way
an adapter does, so every name quackd spells for it lives in its row in
[`catalogue.py`](../../quackd/agent/decision/catalogue.py).

**Nothing here has ever answered a real robot.**

```bash
git clone https://github.com/jaredpalmer/kev    # not run here
cd kev                                          # not run here
uv sync --extra serve                           # not run here: wants uv, and its own floor is Python 3.12
KEV_DTYPE=bf16 uv run --extra serve python -m kev.serve --run jaredpalmer/kev-4b --port 8009   # not run here: pulls the weights from Hugging Face the first time

quackd run arm-grip-check --robot lerobot:mock --decision-llm kev --decision-mode shadow   # not run here: no decision LLM is installed on this machine
```

## The row

| Field | Value |
|---|---|
| `name` | `kev` |
| `backend` | `systemone`, so `SystemOneLLM` over `typesafe_sdk`, and the same `POST /v1/systemone` every other server here speaks |
| `summary` | `Kev, self-hosted: Qwen3.5 with a decision head, on your own GPU` |
| `install` | `git clone https://github.com/jaredpalmer/kev && cd kev && uv sync --extra serve && KEV_DTYPE=bf16 uv run --extra serve python -m kev.serve --run jaredpalmer/kev-4b --port 8009` |
| `url` | `http://127.0.0.1:8009` |
| `key_env` | `none`, a server that wants no key: `systemone.py` sends the literal `local` instead, so a `TYPESAFE_API_KEY` sitting in the same `.env` is never posted to a port on your own machine |
| `model` | `kev-latest` |
| `price` | `none`, which is neither free nor unknown: it is a server you run, and `factory.resolve_decision_price` turns it into the self-hosted rate quackd already charges a local model at |
| `extra` | `decision`, installed with `quackd[decision]` |
| `sdk` | `typesafe_sdk`, the import that says whether that extra is here |

That is `PRESETS["kev"]` in `catalogue.py`. A test reads the address, the model id, the key, the
install line and the extra back off this page and compares them with the row, so a drifting cell
fails the suite. `--decision-url http://127.0.0.1:8008` overrides the address, `--decision-llm
kev:kev-9b` the model id.

## What it is

Qwen3.5 base weights -- 0.8B, 4B or 9B -- frozen, with a rank-16 LoRA adapter and a small pointer
head on top. The checkpoint is fixed **at startup** by `--run`: `jaredpalmer/kev-4b`, `-9b`,
`-0.8b`, a local path, or `jaredpalmer/kev-4b@qwen3` for the older, faster Qwen3 weights; they
download from Hugging Face on first run, so the first request is slow and needs network. It wants
a GPU for the larger two and runs bf16 on Apple silicon, and its own floor is Python 3.12 on
`transformers>=5.17,<6` and `torch>=2.6,<2.9`. Its README measures a median 149 ms for Kev-0.8B
and 721 ms for Kev-4B through MLX on an M5, on a ~270-token state, both inside quackd's
one-second budget before any HTTP is added. Apache-2.0.

## What to know before you point a robot at it

- **`pip install kev` installs something else entirely.** That name on PyPI is an unrelated
  project, "K.E.V. (Keys, Extra Stuff, and Values), a Python ORM for key-value stores", at 0.10.0,
  by Brian Jinwright. `jaredpalmer/kev` is unpublished and ships no console script, hence `python
  -m kev.serve`.
- **The model field is inert.** `kev/api.py` declares `model: str = "kev-latest"`, an
  unvalidated string with a default, and `serve.py`'s handler echoes back whatever you sent:
  its own tests post `jev-latest` and assert the echo, and post `"m"` and get a 200.
  `kev-4b` and `kev-9b` do **not** switch checkpoints.
- **Its code default port is 8008 and every README example passes `--port 8009`.** quackd's row
  says 8009, the documented command being the one people run, and this is exactly the class of
  detail that makes a per-row address better than a shared default. `--decision-url` is how you
  say otherwise.
- **It handles one request at a time.** A single `threading.Lock` wraps the whole forward pass,
  and its README says it caches repeated state text but does not batch callers. One robot is fine;
  a flock pointed at one instance is a queue, and a queue behind a one-second timeout escalates
  every turn.
- **Its confidence is its own arithmetic.** A choice scores `(p_max - 1/K) / (1 - 1/K)`, exactly
  `1.0` at K=1; a score scores `1 - E|level - mode| / (L - 1)`. Its README says TypeSafe's formula
  is not public, that this is an approximation, and that "Neither field is a measured accuracy
  rate." quackd's floors are two of TypeSafe's numbers with two of its own between them, all
  four shaped around Jev and none of them around this ([how a turn is
  decided](../decision-llms.md#how-a-turn-is-decided)).

> [!WARNING]
> Kev has **no authentication of any kind** -- no middleware, no header check, and an
> `Authorization` header is neither required nor read -- with CORS wide open on top of that. Its
> README says to keep it local unless you add authentication yourself, and it can be: the bind
> address is hardcoded and there is no `--host` flag, so reaching it from another machine means a
> reverse proxy, which is then the only thing between a network and an unauthenticated model with
> a robot downstream of it.

## VERIFIED (read from its README and source on 2026-09-22, at `main`)

| Thing | Value | What quackd does with it |
|---|---|---|
| Weights and checkpoint | Qwen3.5 0.8B, 4B or 9B frozen, plus a rank-16 LoRA adapter and a small pointer head; which one is fixed at startup by `--run`, and it downloads from Hugging Face on first use | nothing is imported or vendored: quackd holds an address and a model id, and the row's install line serves `jaredpalmer/kev-4b` |
| Install and the name | clone only, since it is unpublished and ships no console script, hence `python -m kev.serve`; PyPI's `kev` is an unrelated key-value ORM at 0.10.0. Python `>=3.12`, `transformers>=5.17,<6`, `torch>=2.6,<2.9` | the row's install line clones, and the hub's table quotes it verbatim; the pins are the server's own business, `quackd[decision]` being an HTTP client on quackd's Python |
| Bind and port | `uvicorn.run(app, host="127.0.0.1", port=a.port)` at `serve.py`'s `uvicorn.run`, with no `--host` flag; the port defaults to `8008` at `serve.py`'s `--port` default and in the module docstring, while every README example passes `--port 8009` | the row's `url` is `http://127.0.0.1:8009`, spelled `127.0.0.1` rather than `localhost` because a `localhost` resolving to `::1` first is refused with nothing useful said; `--decision-url http://127.0.0.1:8008` is how a reader who ran it bare says otherwise |
| Auth and CORS | nothing in `serve.py` reads a key; `allow_origins=["*"]`, all methods, all headers | `systemone.py` sends `NO_KEY`, the literal `local`, which is what its own README example and `tests/test_api.py` do |
| Routes | `POST /v1/systemone`, `/v1/systemone/permute`, `/v1/systemone/separate`, `GET /v1/models`; no `/health`, and the listing is `{"models": [...]}` rather than OpenAI's | quackd sends the first and reads neither of the others; `quackd doctor` reports the row, never the server |
| Model field and response | `model: str = "kev-latest"` at `api.py`'s request model, echoed back at `serve.py`'s handler; the response is exactly `model`, `answers`, `usage`, `latency_ms` | the record says what quackd asked for, which here is all the field can mean; `_answer` in `stepper.py` reads the mapping's `answers` directly, and there is no `id` or `created` to ignore |
| Noul answers | `{"type": "noul", "noul": <p_true>}`, `api.py`'s noul answer: no confidence and no probabilities | `done` and `need_human` read the raw probability against 0.5, the one place quackd needs no confidence; false is `1 - noul`, as its own `RemotePredictor` derives it |
| Confidence and rounding | choice is `(p_max - 1/K) / (1 - 1/K)` and `1.0` at K=1, score is `1 - E\|level - mode\| / (L - 1)`, and every probability goes through `round_prob()` in `api.py`, which rounds to four decimal places and says why: at the 255-option maximum, `255 * 0.00005` keeps a rounded distribution inside TypeSafe's `|sum - 1| < 0.02` | compared with `FLOORS`: brake 0.50, read 0.60, motion 0.85, confirm 0.90. A probability map often does not sum to 1.0, and its own conformance test allows `abs_tol=0.03`, so quackd records the spread rather than re-normalising it |
| Usage and `latency_ms` | usage is exactly `{input_tokens, output_tokens}`, no `total_tokens`, and the output count is the token length of the serialised answers rather than generation, which its README calls "a billing-style figure"; `latency_ms` times the forward pass inside the mutex only, `serve.py`, around the lock | `_usage` reads `input_tokens`, so a turn is billed measured rather than estimated, and output is multiplied by a rate of 0; quackd times the whole call into `latency_s`, which reads higher and is the figure the timeout judges |
| Limits and truncation | `SERVE_MAX_STATE` and `SERVE_MAX_BRANCH`, both 8192, defined in `model.py` and imported by `serve.py`, the branch budget being 8192 minus the state; choice 1..255, score levels 2..255; training used at most 384 state tokens and 1,024 for a state plus one question. An over-long state is trimmed in silence, because `serve.py` calls `encode()` without `strict=True`, so `model.py`'s guard's guard never fires and the `state_truncated` flag is never surfaced | quackd caps its state at 6,000 characters soft and 24,000 hard, inside both, and `MAX_CALLS_PER_VERB` is 12 against the widest verb's six; it records `state_chars` and what it trimmed, so a long turn is legible from quackd's side even where the server would say nothing |
| Concurrency and latency | one `threading.Lock` around the whole forward pass in `serve.py`. Its own medians, over five questions across three options on a ~270-token state: through MLX on an M5, 0.8B 149 ms and 4B 721 ms on a new state, 28 ms and 136 ms on a repeated one, against 1062 ms and 3302 ms for the same checkpoints on plain PyTorch MPS; on an L4 at bf16, 118 ms for a 101-token request and 189 ms for a 330-token one | one instance per robot, since a flock queues behind the lock; `TIMEOUT_S` is 1.0 s, which both MLX checkpoints clear on a Mac and the PyTorch MPS path does not |
| Calibration and cache | a fitted temperature of about 2.1 to 2.4 applied at load, with `KEV_TEMPERATURE=1.0` for raw logits, which never changes the argmax; a prefix KV cache on exact repeats of states of 384 tokens or more (242 ms against 861 ms at 772 tokens) | a floor is a statement about calibrated probabilities, so leave the temperature alone; quackd's state changes every turn, so assume the slow number |

## UNVERIFIED, and what quackd does about each

| Name | The assumption | What quackd does |
|---|---|---|
| `WIRE_COMPATIBLE` | that its four-key response keeps the shape `typesafe_sdk` parses | the SDK wants the `model` and `usage` keys present and Kev always sends both; quackd's own reader takes an answer as an object or as a plain mapping, and one it cannot read costs the turn as `gate: unreadable` or `gate: error` and hands it to the model rather than raising |
| `CONFIDENCE_MEANS_THE_SAME` | that its confidence is comparable to the one quackd's floors were set against. It is not: the floors (brake 0.50, read 0.60, motion 0.85, confirm 0.90) are two numbers TypeSafe publish with two of quackd's own set between them, all four Jev-shaped and none of them measured here, and this server computes confidence by the formula above | nothing automatic. `--decision-mode shadow` records what it would have chosen without letting it act, which is how the floors get re-tuned |
| `LATENCY_UNDER_ONE_SECOND` | that it answers inside the turn budget. Its own figures put the 4B at 721 ms through MLX on a Mac, inside it with little to spare once HTTP is added, and at 3302 ms on plain PyTorch MPS, outside it. Nobody has measured any checkpoint against a robot | `TIMEOUT_S` is 1.0 s, enforced with `asyncio.wait_for` around `decide()` in `stepper.py` so it holds whatever the backend is; a turn that runs out escalates to the model and the record says how long it waited |
| `USAGE_REPORTED` | whether its token counts mean anything, given that the output count is the answer it serialised rather than anything it generated | where a count is missing or is not a measurement, quackd estimates the request at four characters to the token and marks the turn `usage_estimated`; a self-hosted row is costed at $0 either way |
| `PYTHON_FLOOR` | that its 3.12 floor is somebody else's problem | nothing. `quackd[decision]` is a small HTTP client that runs on quackd's own Python, and the server runs in whatever environment you started it in |
| `SINGLE_FLIGHT` | that one instance is enough | it is for one robot, which is all quackd asks of it: one request a turn. A flock queues behind the lock, and the answer is an instance per robot |
| `STATE_TRUNCATED_SILENTLY` | that the state quackd sends fits | quackd caps its own state at 6,000 characters soft and 24,000 hard and records `state_chars` and what it trimmed, so the size is on the record even on a turn the server would not have said a word about |
| `PIP_NAME_IS_A_TRAP` | that a reader installs the right thing | the row's install line clones rather than pip-installs, for exactly this reason |

## Status

Kev has never answered a real robot, and this page says so until somebody has. The client that
would reach it is exercised against a stub over the same wire (`tests/stub_decision_llm.py`,
`tests/test_decision_llms.py`): that proves quackd reads an answer, a missing usage count and a
timeout as described here, and nothing about the server. Without the extra, `quackd doctor` prints
this row as missing `quackd[decision]`, no key needed, `kev-latest`, `http://127.0.0.1:8009`, and
`--decision-mode shadow` is how that status changes.

## How to help

Run it in shadow mode on your own bench ([measuring it
yourself](../decision-llms.md#measuring-it-yourself)) and report the agreement rate, the latency
against a real robot's state rather than a 270-token benchmark one, and the answers that surprised
you, which is the only one of the three a table cannot produce. Say which checkpoint `--run`
loaded and which interface and port you served it on, since 8008 and 8009 both exist here and a
reverse proxy changes what the warning above is worth. A confidence floor moves on a calibration
plot and nothing else ([how a turn is decided](../decision-llms.md#how-a-turn-is-decided)), and
[CONTRIBUTING.md](../../CONTRIBUTING.md) says where such a change goes.
