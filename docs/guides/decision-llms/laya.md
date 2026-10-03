# Laya

Laya, in this process: an encoder off Hugging Face, no server and no key. Made by
[NandhaKishorM/laya](https://github.com/NandhaKishorM/laya), Apache-2.0, read at `main` on
2026-09-22 against 0.3.5 on PyPI (fifteen releases, 0.1.0 through 0.3.5). It is the row that is
not a server, and the proof that this seam is a protocol and not an HTTP call: the same four
questions reach it unchanged and there is nowhere to send them. Every name quackd spells for it
lives in its row in [`catalogue.py`](../../quackd/agent/decision/catalogue.py), a decision LLM
having no `upstream_api.py`.

**Nothing here has ever answered a real robot.**

```bash
uv pip install "quackd[laya]"   # not run here; nothing to start after it, and no port and no key
quackd run arm-grip-check --robot lerobot:mock --decision-llm laya --decision-mode shadow  # not run here: no decision LLM is installed on this machine
```

## The row

| Field | Value |
|---|---|
| `name` | `laya` |
| `backend` | `laya`, the `IN_PROCESS` constant, not `systemone`: it is imported, never posted to |
| `summary` | `Laya, in this process: an encoder off Hugging Face, no server and no key` |
| `install` | `uv pip install "quackd[laya]"`, quoted because an unquoted `[laya]` is a glob the shell eats before pip sees it. It pulls torch, and the weights download on first use |
| `url` | `none`, because it has no address at all and nothing leaves the machine |
| `key_env` | `none`; the field names a server that wants no key, and this is not a server, so not even the literal `local` the HTTP client would send goes anywhere |
| `model` | `typed-decisions` |
| `price` | `none`, which is neither free nor unknown: `factory.resolve_decision_price` turns it into the self-hosted rate quackd already prices a model on your own machine at |
| `extra` | `laya`, so `quackd[laya]` |
| `sdk` | `laya`, the import name that says whether the extra is here |

That is `PRESETS["laya"]` in [`catalogue.py`](../../quackd/agent/decision/catalogue.py), and a
test reads the url, the model, the key, the install line and the extra back off this page.
`--decision-url` overrides the address, which here overrides nothing because there is none to
move, while `--decision-llm laya:multilingual` overrides the model id.

## What it is

Three checkpoints, all pulled from Hugging Face the first time one is asked for: `english`
(`convaiinnovations/laya`, 421M, ModernBERT-large, 512 tokens), `multilingual`
(`convaiinnovations/laya-multilingual`, 322M, mmBERT-base, 1,024 tokens, 100+ languages) and
`typed-decisions` (`convaiinnovations/laya-typed-decisions`, 421M, ModernBERT-large, 1,024
tokens), which is the one quackd asks for. The bundle repo holds all three, `english` at the root
and the other two in subfolders, and `snapshot_download` fetches only the one asked for. It needs
no GPU, which is the point of it: the order is `cuda -> mps -> cpu`, an unavailable device warns
and falls back, an out-of-memory falls back at runtime too, and float32 is forced on `cpu` and
`mps`. Nobody has timed it against a robot; its own README measures 33 ms for a single question
on a T4 and 7.2 ms a question batched, against a model reload at 7.4 s on a CPU and 10.3 s on a
T4. The question fits the budget at the seam many times over and the reload is seven times it,
so what has to be kept off a turn is the load rather than the inference. Apache-2.0, Python 3.10
and up.

## What to know before you point a robot at it

- **The model name quackd sends is `typed-decisions`, not `laya`**, and it is the fact most worth
  being exact about. `predict(model=...)` goes through `normalise_name()`, which lowercases,
  strips and then looks up a closed alias table, raising `ValueError` on anything else, so a
  Hugging Face repo id is rejected and the bare name `laya` is an alias for the plain **English**
  checkpoint rather than the decision-tuned one.
- **The Router's defaults are built for a notebook, not for a run.** `max_loaded=1` with
  `preload=False` evicts on every model switch, a reload its own README measures at 7.4 s against
  a one-second budget. quackd builds `Router(preload=True, default=<the model>)` so what is
  preloaded is the checkpoint the run will ask for, falling back to `Router(preload=True,
  max_loaded=2)` where that keyword is not accepted.
- **It loads lazily and calls off the loop.** The load happens on the turn that first needs it,
  because paying seven seconds while the CLI parses would stall every run that never reaches a
  turn, and a load that failed is remembered so a broken install costs one attempt rather than one
  a turn. `predict` is a synchronous `@torch.no_grad()` forward pass, so quackd calls it through
  `asyncio.to_thread`: the loop it would block is the one holding the robot's deadman.
- **Its confidence is two scales under one key, and neither is Jev's.** A choice or a score
  reports normalised Shannon entropy, `1 - H(p)/log k`, 0.0 to 1.0; a noul reports
  `round(max(p_true, 1 - p_true), 4)`, which floors at 0.5 and never drops below it. quackd reads
  a noul's raw probability against `DONE_THRESHOLD` and `HUMAN_THRESHOLD`, both 0.5, and not its
  confidence at all. quackd's floors are two of TypeSafe's numbers with two of its own between
  them, all four shaped around Jev, while this one computes confidence by its own formula, so
  anybody tuning one from these numbers needs to know the two are not comparable: [how a turn
  is decided](../decision-llms.md#how-a-turn-is-decided).
- **A wide allowlist can raise rather than answer.** `head_max_len` is 192 tokens and each option
  is truncated to 48, the state getting what is left; options that do not fit make `system_one`
  raise `ValueError("question %r options exceed head_max_len=%d")`, which quackd records as `gate:
  error` and hands to the model, so it costs a turn rather than a run.

> [!WARNING]
> **Some of its shipped calibration is wrong by its own account.** `common.py` clamps checkpoint
> temperatures to `[0.5, 5.0]` and emits a `RuntimeWarning` naming the buckets it rejected, and
> its own source comment states that the shipped `choice:11+` bucket is 0.1006, a temperature that
> would publish a 0.24 top probability as 0.99. Treat a confidence out of an affected bucket as
> uncalibrated.

## VERIFIED (read from its README and source on 2026-09-22, laya 0.3.5 with the repository at `main`)

| Thing | Value | What quackd does with it |
|---|---|---|
| No HTTP layer at all | no `/v1/systemone`, no FastAPI, uvicorn, flask, starlette or aiohttp import, no `app =` and no `async def` anywhere; checked twice, by a grep over every source file and the README and against the full 49-blob repository tree | reached through quackd's own `laya.py` rather than `systemone.py`: no port, no key, and no request leaving the machine |
| Licence, releases, install | `Apache-2.0`; fifteen releases on PyPI, 0.1.0 through 0.3.5; `requires-python >= 3.10`; pulls `torch>=2.0`, `transformers>=4.48`, `safetensors`, `huggingface_hub` and `numpy` | the pin this page quotes, and why the extra is separate from `quackd[decision]` and why neither is in `quackd[all]`: a decision LLM nobody asked for should not drag torch into an install |
| `english` and `multilingual` | `convaiinnovations/laya`, 421M, ModernBERT-large, 512 tokens; and `convaiinnovations/laya-multilingual`, 322M, mmBERT-base, 1,024 tokens, 100+ languages | the first is what the bare alias `laya` resolves to and is not what quackd asks for; the second is `--decision-llm laya:multilingual` |
| `typed-decisions` | `convaiinnovations/laya-typed-decisions`, 421M, ModernBERT-large, 1,024 tokens; a subfolder of the bundle repo, fetched alone by `snapshot_download` | the row's `model`, and what `laya.py` passes as `default` so the preload is the checkpoint the run will use |
| Device resolution | `cuda -> mps -> cpu` with no device argument; an unavailable explicit device warns and falls back; an OOM in `.to(device)` or in the forward pass moves the model to CPU at runtime; float32 and no autocast on `cpu` and `mps` | CPU-only is fully supported, which is what makes this the row you can run with no GPU at all |
| The names it accepts | a closed alias table through `normalise_name()`: `en` / `laya` / `default` to `english`, `multi` / `ml` / `laya-multilingual` to `multilingual`, `typed` / `typed_decisions` / `laya-typed-decisions` / `decisions` to `typed-decisions`, anything else a `ValueError`; `auto_task_detection` defaults to `False` and when `True` wants an exact question-id set match against one of four hardcoded workflow signatures | quackd sends the canonical `typed-decisions` and a Hugging Face repo id would be refused; the decision-tuned checkpoint is only ever reached by passing `model=`, so the row does |
| Router and `predict` | `Router(models=None, device=None, token=None, max_loaded=1, default="english", auto_task_detection=False, preload=False, standalone_repos=False)`, whose defaults its README measures reloading at 7.4 s on a CPU and 10.3 s on a T4; `predict` itself is synchronous, `@torch.no_grad()`, every question in one batched forward pass, one row per question, with an `RLock` over load, unload and the LRU (its own tests prove 8 concurrent `load()` calls build exactly one agent) and inference explicitly left outside that lock | the signature is read with `inspect.signature` before `default` is passed, so an older Router gets `Router(preload=True, max_loaded=2)` rather than a `TypeError` that a real failure inside somebody's `Router` could hide in; against `TIMEOUT_S` of 1.0 s in `base.py`, any server-shaped use needs one or the other; and the forward pass goes through `asyncio.to_thread` in `laya.py` so the deadman's loop keeps running |
| Result dict | `{"model": "laya-rl-agent", "answers": {...}, "usage": {"input_tokens": int, "output_tokens": 0}}`, plus `"routing"` through `Router.predict` and a per-answer `action` sub-dict `{"act_probability": float}` that is not in the wire format. `result["model"]` is that hardcoded literal on every call whatever ran. `input_tokens` is a real tokeniser count, `int(b["attention_mask"].sum())`, and `output_tokens` is a hardcoded `0` | `stepper._answer` takes a plain mapping as readily as an SDK object; the record says the checkpoint quackd asked for, since the one that answered is only in `result["routing"]["model"]`; `_usage` reads `input_tokens`, so this is one of the two rows whose turns are costed **measured** rather than estimated and the log prints the count with no `~`. The zero output is discarded as a field never filled in, which never reaches the flag, and the cost is $0 regardless because a checkpoint in your own process is charged at the self-hosted rate; `act_probability` is ignored |
| Confidence | choice and score are normalised Shannon entropy, `1 - H(p)/log k`, 0.0 to 1.0; a noul is `round(max(p_true, 1 - p_true), 4)`, floored at 0.5, and carries no `probabilities` where a choice and a score do | a choice is read against `FLOORS` (brake 0.50, read 0.60, motion 0.85, confirm 0.90); a noul's confidence is not read at all, only its raw probability against `DONE_THRESHOLD` and `HUMAN_THRESHOLD`. The choice probabilities are read too, so its answers do get the `decide?` runner-up line in the log, which a noul on its own would not |
| Questions, limits, calibration | `type` and `instructions` required, read as `qdef["type"]` so a missing key is a `KeyError`, `criteria` optional; the types are exactly `choice`, `score` and `noul`; state may be a string, a dict or a list; `max_len` 512 on `english` and 1,024 on the other two, `head_max_len` 192, each option truncated to 48 tokens; and `common.py` clamps checkpoint temperatures to `[0.5, 5.0]` with a `RuntimeWarning` naming the rejected buckets, its own comment putting the shipped `choice:11+` bucket at 0.1006 | quackd's four questions are the same plain dicts every other preset gets, over the flat mapping of short English strings `stepper._build_state` builds; options that do not fit raise, and `Stepper.advise` catches that as `gate: error`; and the warning above stands, since a confidence out of an affected bucket is not a number to tune a floor with |

## UNVERIFIED, and what quackd does about each

| Name | The assumption | What quackd does |
|---|---|---|
| `WIRE_COMPATIBLE` | that its result keeps the shape quackd's reader parses, though nothing here goes near `typesafe_sdk` | the SDK wants the `model` and `usage` keys present and lets their contents be null; quackd's own reader takes an answer as an object or as a plain mapping, and one it cannot read costs the turn as `gate: unreadable` or `gate: error` and goes to the model rather than raising |
| `CONFIDENCE_MEANS_THE_SAME` | that its confidence is comparable to the one quackd's floors were set against. It is not: the floors (brake 0.50, read 0.60, motion 0.85, confirm 0.90) are two numbers TypeSafe publish with two of quackd's own set between them, all four Jev-shaped and none of them measured here, and this one computes confidence by its own formula | nothing automatic. `--decision-mode shadow` records what it would have chosen without letting it act, which is how the floors get re-tuned |
| `LATENCY_UNDER_ONE_SECOND` | that a forward pass answers inside the turn budget on whatever CPU you have. Nobody has measured it against a robot | `TIMEOUT_S` is 1.0 s, enforced with `asyncio.wait_for` around `decide()` in `stepper.py` so it holds whatever the backend is; a turn that runs out escalates to the model and the record says how long it waited |
| `USAGE_REPORTED` | that `int(attention_mask.sum())` is the number you would want billed. It is what the model actually read, which is the honest answer for a forward pass and not the same thing as a hosted API's notion of an input token, and the zero output is a constant rather than a count | quackd takes it as a measurement, so these turns are recorded without `usage_estimated` while a row that counts nothing is recorded with it. It costs nothing either way: a checkpoint in this process is charged at the self-hosted $0 |
| `PYTHON_FLOOR` | that its own interpreter floor is its own | nothing. `quackd[laya]` is an import into quackd's own process, on quackd's own Python |
| `ALIAS_TABLE_IS_CLOSED` | that `typed-decisions` keeps resolving | an unknown name raises `ValueError` rather than falling back to `english`, which is the right direction: a rename would refuse the run rather than quietly answer with the wrong checkpoint |
| `OPTIONS_OVER_HEAD_MAX_LEN_RAISE` | that quackd's labels fit in 192 tokens | a wide allowlist is where they would not, and that turn becomes `gate: error` and goes to the model; `MAX_CALLS_PER_VERB` of 12 bounds one verb's share of it |
| `NOUL_CONFIDENCE_FLOOR_IS_HALF` | that a confidence is a confidence | on a noul this one cannot go below 0.5, so no floor should be read against it; quackd reads the raw probability instead |

## Status

It has never answered a real robot. The client is exercised against a stub rather than the real
package: `tests/stub_decision_llm.py` stands in for it and `tests/test_decision_llms.py` drives
the seam, the lazy load, the remembered failure and the `to_thread` hop included. On a machine
without the extra, `quackd doctor` prints the row as `missing (quackd[laya])`, `none needed` for
the key, `typed-decisions` for the model and `in this process` where every other row has a URL.
`--decision-mode shadow` is how that changes: it asks every turn and acts on nothing.

## How to help

Run it in shadow mode on your own bench and report what came back: [measuring it
yourself](../decision-llms.md#measuring-it-yourself) has the commands. The numbers worth sending
are the agreement rate against what the model chose, the latency per turn on the hardware you ran
it on, and the answers that surprised you. Say which checkpoint you asked for, whether it ran on
`cuda`, `mps` or `cpu`, and whether a turn came back as `gate: error` with the `head_max_len`
message, which is a function of how many calls your body put on offer. If a floor looks wrong,
[how a turn is decided](../decision-llms.md#how-a-turn-is-decided) has them, and a shadow record
moves one.
