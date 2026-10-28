# Who makes what

The same lists as the other pages, turned around: every company and project whose models quackd
can use, which of the three roles each one fills, and the key and the extra it needs. A company
that fills two roles, as OpenAI does, is one row.

<!-- generated: vendor-roles, by scripts/models_docs.py from the three catalogues. Change a row there and run the script, not this table. -->

| Who | Pilot, `--llm` | Decision LLM, `--decision-llm` | Learned policy, its `type` | Key | Extra |
|---|---|---|---|---|---|
| Anthropic | `anthropic` |   |   | `ANTHROPIC_API_KEY` | `quackd[anthropic]` |
| OpenAI | `openai` | `openai` |   | `OPENAI_API_KEY` | `quackd[openai]` |
| Google | `gemini` |   |   | `GEMINI_API_KEY`, or `GOOGLE_API_KEY` | `quackd[gemini]` |
| xAI | `grok` |   |   | `XAI_API_KEY` | `quackd[grok]` |
| Mistral AI | `mistral` |   |   | `MISTRAL_API_KEY` | `quackd[mistral]` |
| DeepSeek | `deepseek` |   |   | `DEEPSEEK_API_KEY` | `quackd[deepseek]` |
| Cohere | `cohere` |   |   | `COHERE_API_KEY`, or `CO_API_KEY` | `quackd[cohere]` |
| Alibaba Cloud | `qwen` |   |   | `DASHSCOPE_API_KEY` | `quackd[qwen]` |
| Moonshot AI | `kimi` |   |   | `MOONSHOT_API_KEY` | `quackd[kimi]` |
| Z.ai | `glm` |   |   | `ZAI_API_KEY` | `quackd[glm]` |
| Meta | `meta` |   |   | `META_API_KEY`, or `MODEL_API_KEY` | `quackd[meta]` |
| OpenRouter | `openrouter` |   |   | `OPENROUTER_API_KEY` | `quackd[openrouter]` |
| any server that speaks OpenAI's API | `local` |   |   | none needed | `quackd[openai]` |
| Ollama | `ollama` |   |   | none needed | `quackd[openai]` |
| vLLM | `vllm` |   |   | none needed | `quackd[openai]` |
| llama.cpp | `llamacpp` |   |   | none needed | `quackd[openai]` |
| LM Studio | `lmstudio` |   |   | none needed | `quackd[openai]` |
| TypeSafe |   | `jev` |   | `TYPESAFE_API_KEY` | `quackd[decision]` |
| Kev (jaredpalmer/kev) |   | `kev` |   | none needed | `quackd[decision]` |
| Von (von-sdk) |   | `von` |   | none needed | `quackd[decision]` |
| OpenJev (razorback16/openjev) |   | `openjev` |   | none needed | `quackd[decision]` |
| OpenDecision |   | `opendecision` |   | none needed | `quackd[decision]` |
| any server that speaks System One |   | `local` |   | none needed | `quackd[decision]` |
| Laya |   | `laya` |   | none needed | `quackd[laya]` |
| Tony Z. Zhao (ALOHA) |   |   | `act` | none needed | `quackd[lerobot-vla]` |
| Hugging Face |   |   | `smolvla` | none needed | `quackd[lerobot-vla]` |
| Physical Intelligence |   |   | `pi05`, `pi0` | a Hub token granted the gated tokenizer it names | `quackd[lerobot-vla]` |
| Jinliang Zheng et al. (2toINF) |   |   | `xvla` | none needed | `quackd[lerobot-vla]` |
| NVIDIA |   |   | `groot` | a Hub token granted the gated tokenizer it names | `quackd[lerobot-vla]` |

<!-- end generated: vendor-roles -->

## Notes

Only where something is worth knowing before you sign up or install.

### Google

`GEMINI_API_KEY`, and quackd reads `GOOGLE_API_KEY` when that is empty, as Google's own SDK does.
The catalogue carries Gemini Robotics-ER 2 as a pilot row, `gemini:gemini-robotics-er-2-preview`,
which is Google's embodied-reasoning model and a planner rather than a VLA. pi0 and pi05 use
Google's PaliGemma tokenizer, `google/paligemma-3b-pt-224`, which is gated on the Hub: accept its
licence there, and give the machine that serves them a Hub token that has been granted it.

### OpenAI

One key, `OPENAI_API_KEY`, and one SDK fill two roles: the pilot, `--llm openai`, and a decision
LLM, `--decision-llm openai`, which is OpenAI's Decisions API and the only decision LLM quackd
may show the camera, with `--decision-frames`.

### Hugging Face

Hugging Face does not fill one role so much as carry the third. Every policy quackd serves is
fetched from the Hub at a revision you name, through LeRobot, Hugging Face's robotics library,
and SmolVLA is Hugging Face's own. A Hugging Face Inference Endpoint can run quackd's policy
server on a GPU Hugging Face rents by the hour, through `quackd policy endpoint`, which has met
only the test suite's stand-ins. A Hub token, from `hf auth login` or `HF_TOKEN`, is what reaches
a gated repository and what starts and reaches an endpoint ([vlas.md](vlas.md)).

### NVIDIA

GR00T N1.7 is served as LeRobot's `groot` type. Its backbone's tokenizer,
`nvidia/Cosmos-Reason2-2B`, is gated on the Hub, and its weights are under the NVIDIA Open Model
License. An NVIDIA Jetson is something else: a board quackd reaches with `--host` for its model
server, camera and detector ([guides/jetson.md](../guides/jetson.md)).

### Physical Intelligence

pi0 and pi05 are served as LeRobot's ports of them. pi0-FAST is not, because its policy runs the
FAST tokenizer's own code from the Hub, and the Pi API, a hosted beta behind a waitlist since
2026-10-13, has no public protocol to speak ([vlas.md](vlas.md#not-served-and-why)).

### TypeSafe

Jev, `--decision-llm jev`, is the decision LLM the System One wire format is named after, hosted
by TypeSafe, with `TYPESAFE_API_KEY` and `quackd[decision]`.

### OpenRouter

One key, `OPENROUTER_API_KEY`, for other vendors' models as the pilot, and a turn is costed at
what OpenRouter billed. **No OpenRouter model has answered a real quackd request.** Its guide
is [guides/openrouter.md](../guides/openrouter.md).

### Black Forest Labs

FLUX 3 Action has an official SO-101 checkpoint, and quackd does not serve it yet: it is on
LeRobot's main branch only and wants a large NVIDIA GPU, and its licence grants non-commercial
use, with commercial use a question for its makers
([guides/policies.md](../guides/policies.md#flux-3-action)).

### Local servers and your own decision LLMs

Ollama, vLLM, llama.cpp, LM Studio and any other server that speaks OpenAI's API serve the pilot
on your own machine with no key ([guides/local-llms.md](../guides/local-llms.md)). Kev, Von,
OpenJev and OpenDecision are decision LLMs you run yourself, `local` is any other server that
speaks System One, and Laya runs inside quackd's own process
([guides/decision-llms/README.md](../guides/decision-llms/README.md)).
