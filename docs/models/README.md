# Models

A Roboglot run has up to three models in it, and only the first is required. The robots are in
[adapters/](../adapters/status.md), one page per body. This folder is the other half: the models
that plan, the ones that decide, the ones that move the arm, and how to name each one.

<!-- generated: roles, by scripts/models_docs.py from roboglot/agent/providers/catalogue.py and roboglot/adapters/policies.py. Change a row there and run the script, not this table. -->

| Role | Named with | Required | What Roboglot names | Page |
|---|---|---|---|---|
| **Pilot LLM** | `--llm VENDOR[:LLM]` | yes, and `fake`, the scripted pilot, flies when nothing names one | 122 ids from twelve cloud vendors, more through OpenRouter's own list, and five local presets | [pilot-llms.md](pilot-llms.md) |
| **Decision LLM** | `--decision-llm NAME[:LLM]` | no, and off unless a run names one | seven decision LLMs, hosted or your own | [decision-llms.md](decision-llms.md) |
| **Learned policy** | `--policy-url`, to a `roboglot policy serve` or an endpoint as `hf:NAME` | no, and the SO-101 arm alone takes one | six LeRobot policy types, five of them VLAs, served from the Hub | [vlas.md](vlas.md) |

<!-- end generated: roles -->

## How one run uses all three

On the SO-101 arm, registered as `arm-01`, with a policy server running beside it:

```bash
roboglot run task.glot --robot arm-01 --llm openai --decision-llm openai --decision-mode shadow --policy-url http://127.0.0.1:9875
```

OpenAI's `gpt-6.1-sol` is the pilot: it reads the arm's manifest, plans and calls the arm's verbs.
OpenAI's Decisions API is asked every turn in shadow and changes nothing, so the run's record
shows what it would have answered. When the pilot calls `manipulate`, the policy the server
serves drives the arm for one segment, at the arm's own rate, with every goal held to the arm's
travel before it is sent.

## How to choose

- **No key, no network:** `--llm fake`, the scripted pilot. It plays the starter tasks and is
  what the test suite runs.
- **From a chat:** `roboglot serve-mcp`, and the chat client's own LLM is the pilot. Roboglot
  selects no LLM at all ([guides/mcp.md](../guides/mcp.md)).
- **One cloud key:** that vendor's default, `--llm anthropic` or `--llm openai` and so on, or an
  LLM of its own from [pilot-llms.md](pilot-llms.md).
- **One key for many vendors:** `--llm openrouter`, which reaches other vendors' LLMs
  ([guides/openrouter.md](../guides/openrouter.md)). No OpenRouter LLM has answered a real
  Roboglot request yet.
- **Your own machine or GPU, or no network:** a local preset, `--llm ollama`, `vllm`,
  `llamacpp`, `lmstudio` or `local` ([guides/local-llms.md](../guides/local-llms.md)).
- **Fewer, cheaper pilot turns:** a decision LLM in front of the pilot, in shadow first
  ([decision-llms.md](decision-llms.md)).
- **Contact work on the arm:** a learned policy, served beside the arm, on a rented GPU or on a
  Hugging Face Endpoint ([vlas.md](vlas.md)).

None of this is a ranking. Which LLM flies a given body best is not something Roboglot has
measured, and the pages say what has actually run.

## Who makes what

[vendors.md](vendors.md) is the same lists turned around: each company and project, which of
the three roles it can fill, the key and the extra it needs.

## Where the lists live

Every table of models on these pages is rendered from the code that decides what Roboglot
takes, by [`scripts/models_docs.py`](../../scripts/models_docs.py), and a test fails when a
page and the code disagree:

- the pilots: [`roboglot/agent/providers/catalogue.py`](../../roboglot/agent/providers/catalogue.py),
  with the keys, extras and addresses in
  [`factory.py`](../../roboglot/agent/providers/factory.py) and the local presets in
  [`local.py`](../../roboglot/agent/providers/local.py);
- the decision LLMs: [`roboglot/agent/decision/catalogue.py`](../../roboglot/agent/decision/catalogue.py);
- the learned policies: [`roboglot/adapters/policies.py`](../../roboglot/adapters/policies.py), held
  by a test to the types the policy server reads.

So a model is added, renamed or repriced there, and the script run, never in a table here. `roboglot
list-models` prints the same rows on a terminal, `--kind decision` and `--kind policy` for the
other two.

## The guides

These pages say what there is. How to set one up is in a guide:
[local LLMs](../guides/local-llms.md), [OpenRouter](../guides/openrouter.md),
[decision LLMs](../guides/decision-llms/README.md), with a page for each one Roboglot names, and
[policies](../guides/policies.md), which serves one on the laptop, on a rented GPU or on a
Hugging Face Endpoint.
