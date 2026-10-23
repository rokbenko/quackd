# Draft: feedback on Nebius and NVIDIA tools

For the feedback section of the Devpost form. Everything below was found while building this
entry; add what the live runs show before pasting.

## Nebius Token Factory

* **Two base URLs.** The Nemotron page shows `https://api.tokenfactory.us-central1.nebius.com/v1/`
  while the docs use `https://api.tokenfactory.nebius.com/v1/`. Neither says when to use which.
* **The API examples predate Nemotron.** Function calling uses Llama 3.1 8B, vision uses Qwen2
  VL, and the model list example shows only Llama 3.1. A Nemotron example per page would help
  most in a hackathon about Nemotron.
* **The model list cannot say which models take tools.** `supported_features` is free text with
  no documented values, and the pricing strings have no documented unit, so a client has to try
  a model to learn whether it calls tools, and cannot compute a cost.
* **Model ids differ in case between catalogues** (`nvidia/Nemotron-3-Nano-Omni` and
  `nvidia/nemotron-3-nano-omni`), and the API matches ids exactly.
* **The structured output page shows two request shapes** for `json_schema` (the schema itself,
  and one wrapped in `name`, `schema` and `strict`). Its Python snippet misses a comma, and one
  cURL example spells the field `"response format"`.
* **A security note worth a line in the quickstart.** The OpenAI SDK reads
  `OPENAI_CUSTOM_HEADERS`, `OPENAI_ORG_ID` and `OPENAI_PROJECT_ID` from the environment by
  itself. With the quickstart's `OpenAI(base_url=..., api_key=...)`, openai 3.36.1 sent a
  gateway credential from `OPENAI_CUSTOM_HEADERS` in place of the Nebius key, plus an OpenAI
  organization and project, to Token Factory.
* **No NVIDIA physical AI model to call.** Cosmos Reason and GR00T are not on Token Factory, and
  custom weights on dedicated endpoints are a beta on request, so a Physical AI entry reaches for
  Nemotron or hosts Cosmos itself on a GPU.

## Nebius AI Cloud

* **CPU jobs are possible but undocumented in the jobs pages.** The jobs quickstart shows only a
  GPU preset; the CPU platforms (`cpu-d3`, `cpu-e2`) and their presets are on the Compute VM types
  page, and the 20 GiB disk minimum for CPU jobs is only on the manage page.
* **`--env-secret` needs the secret's payload key to equal the variable's name.** The CLI
  reference describes the selector but not this, which the manage page states.
* **The rules count DevPods** as AI Cloud compute, but the docs I found call the interactive
  environments Devlabs, and never mention DevPods.

## NVIDIA models

* To fill in after the live runs: how each Nemotron size follows quackd's tool list, how often a
  tool call arrives as text rather than as a call, and what the vision rows make of a robot.
