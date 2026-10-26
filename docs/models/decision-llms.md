# Decision LLMs

A decision LLM is the optional second model of a run. It sits in front of the pilot as a
discrete stepper and answers the turns that are a choice among calls the body already has, a
read, the brake, the gripper or a gaze, with a typed value and a probability for each option.
It writes nothing: no joint angle, no sentence and no verdict, all of which stay the pilot's. A
turn it is not sure enough about goes to the pilot as before. Why it exists, how a turn is
decided and what it is never allowed to do are in
[the decision LLMs guide](../guides/decision-llms/README.md).

> [!IMPORTANT]
> **It is off unless a run names one.** quackd installs no decision LLM, needs no key for one
> and never switches one on because it found a key in a `.env`. Start in shadow
> (`--decision-mode shadow`): the decision LLM is asked every turn and changes nothing, and the
> run's record says what it would have answered beside what the pilot did.

## The ones quackd names

<!-- generated: decision-rows, by scripts/models_docs.py from quackd/agent/decision/catalogue.py. Change a row there and run the script, not this table. -->

| `--decision-llm` | What it is | Wire | Runs | Key | Extra | Address | Default model | Frames | USD per Mtok in / out | Guide |
|---|---|---|---|---|---|---|---|---|---|---|
| `jev` | TypeSafe's Jev, hosted: the System One model the format is named after | System One, `POST /v1/systemone` | hosted, by its maker | `TYPESAFE_API_KEY` | `quackd[decision]` | the SDK's own | `jev-1.13.0` | no | 0.042 / 0, published | [jev](../guides/decision-llms/jev.md) |
| `openai` | OpenAI's Decisions API, hosted: gpt-6-luna over POST /v1/decisions | OpenAI's Decisions API, `POST /v1/decisions` | hosted, by its maker | `OPENAI_API_KEY` | `quackd[openai]` | `https://api.openai.com` | `gpt-6-luna` | with `--decision-frames` | 0.1 / 0, published | [openai](../guides/decision-llms/openai.md) |
| `kev` | Kev, self-hosted: Qwen3.5 with a decision head, on your own GPU | System One, `POST /v1/systemone` | a server you run | none | `quackd[decision]` | `http://127.0.0.1:8009` | `kev-latest` | no | nothing billed: it is yours | [kev](../guides/decision-llms/kev.md) |
| `von` | Von, self-hosted: a 395M encoder, about 18 ms on a GPU, runs on CPU too | System One, `POST /v1/systemone` | a server you run | none | `quackd[decision]` | `http://127.0.0.1:8000` | `von-latest` | no | nothing billed: it is yours | [von](../guides/decision-llms/von.md) |
| `openjev` | OpenJev, self-hosted: DiffusionGemma behind vLLM, or MLX on Apple silicon | System One, `POST /v1/systemone` | a server you run | none | `quackd[decision]` | `http://127.0.0.1:8080` | `openjev-latest` | no | nothing billed: it is yours | [openjev](../guides/decision-llms/openjev.md) |
| `opendecision` | OpenDecision, self-hosted: a zero-shot encoder that runs without a GPU | System One, `POST /v1/systemone` | a server you run | none | `quackd[decision]` | `http://127.0.0.1:8000` | `opendecision` | no | nothing billed: it is yours | [opendecision](../guides/decision-llms/opendecision.md) |
| `local` | any other server that speaks /v1/systemone, at --decision-url | System One, `POST /v1/systemone` | a server you run, at `--decision-url` | none | `quackd[decision]` | the one you give | the server's own | no | nothing billed: it is yours | [local](../guides/decision-llms/local.md) |
| `laya` | Laya, in this process: an encoder off Hugging Face, no server and no key | none: asked in this process | in this process | none | `quackd[laya]` | none | `typed-decisions` | no | nothing billed: it is yours | [laya](../guides/decision-llms/laya.md) |

<!-- end generated: decision-rows -->

`--decision-llm NAME[:MODEL]` names one, `--decision-url` moves its address, `--decision-mode
off|shadow|on` says what its answers may do and `--decision-frames` lets the one row that may see
be shown the camera, at OpenAI's own hosts and only on a run that asks. Each has a variable of
its own (`QUACKD_DECISION_LLM`, `QUACKD_DECISION_URL`, `QUACKD_DECISION_MODE`,
`QUACKD_DECISION_FRAMES`), and the flags win. How to get each server running is on its own page,
the last column, and in the guide's table, which quotes each row's install line. A server that
speaks System One and is not in the table is `--decision-llm local --decision-url
http://host:port`, and a decision LLM with an API of its own can add itself through the
`quackd.decision_llms` entry point group
([writing a plugin](../guides/decision-llms/README.md#writing-a-plugin)).

## Choosing one

- **Hosted, nothing to run:** `jev`, with `TYPESAFE_API_KEY`, or `openai`, which uses the
  `OPENAI_API_KEY` and the `openai` SDK the pilot may already use.
- **A server of your own:** `kev` and `openjev` want a GPU, `von` and `opendecision` run on a
  CPU too, and `local` is any other server that speaks the same wire format.
- **No server at all:** `laya` loads an encoder into this process and pulls torch to do it.

Which turns a decision LLM may answer depends on the body, and on the SO-101 arm it is few of
them: the guide's [SO-101 section](../guides/decision-llms/README.md#the-so-101-arm-in-four-parts)
says which, and what that is worth in seconds and dollars.

## What has run

**Nothing here has ever answered a real robot.** Every row has been exercised against the mock
arm, the simulators and the test suite, with a stand-in answering for the server, and not one
has driven hardware. Each row's page says what was read from its upstream and when, and what is
still assumed.
