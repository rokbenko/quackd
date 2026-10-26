# ADR-0060: Hugging Face serves many VLAs, one family read at a time

**Status:** accepted · **Date:** 2026-10-25 · Extends [ADR-0048](0048-policies-are-the-arms-executor.md) (the policy server serves the types whose processors it has read, and each type now allows its own steps and no other type's) · Implemented in `adapters/lerobot/src/quackd_lerobot/policy/pipeline.py` (`STEPS_BY_TYPE`, `REFUSED_STEPS`, `nested_patterns`, `check_family`), `policy/upstream_api.py`, and `quackd/adapters/policies.py` for what is served and what is not ([policies](../guides/policies.md#the-policies))

## Context

On 2026-10-25 Rok asked for one vendor that serves many VLAs, the way `--llm openrouter` reaches
many language models behind one key. The research that day found none:

- Hugging Face's serverless Inference Providers serve no robotics model at all. The Hub's
  `models?pipeline_tag=robotics&inference_provider=all` answered `[]`.
- NVIDIA hosts no inference API for GR00T.
- Physical Intelligence's Pi API, a beta since 2026-10-13, serves its own models behind a
  waitlist and publishes no protocol.
- The Gemini Robotics VLA is offered to partners only. Gemini Robotics-ER 2 is public, and it
  is a planner, which quackd already carries as a pilot row (`gemini-robotics-er-2-preview`)
  and nothing on record has flown.
- vLLM-Omni serves several VLAs over OpenPI's WebSocket protocol, but it is a server you run,
  not a vendor.

What does play OpenRouter's part is LeRobot and the Hub. One library and one checkpoint format
carry 19 policy types at LeRobot 0.6.1, the release quackd's server pins, and the Hub holds
hundreds of SO-101 fine-tunes of them. quackd's server served three, ACT, SmolVLA and pi05,
and refused every other type before a byte of its weights was fetched, because a checkpoint's
processors are code (`PROCESSOR_CLASS_IMPORT`) and each type's steps were read before it was
served.

Reading for more families turned up two things wrong with what the server already did.

- **The allowlist was one list for every type.** A step any of the three types used was a step
  all three could name, so an ACT could name pi05's tokenizer, which nobody had read for an
  ACT. Each family added to that one list would have widened it for every other: GR00T N1.7's
  processor step loads its tokenizer trusting a repository's own code, hard-wired.
- **A tokenizer came with its model's weights.** A model a processor step names, a tokenizer,
  was fetched with its safetensors, as a backbone a policy is built from has to be. pi05 names
  `google/paligemma-3b-pt-224` for its tokenizer, so serving one fetched 11.69 GB of PaliGemma's
  weights to load 21.9 MB of tokenizer files, sizes read off the Hub on 2026-10-25.

## Decision

1. **A step is allowed by the type that names it.** `STEPS_BY_TYPE` gives each served type
   LeRobot's default steps and its own family's, each read at the pin
   and named by a ref of its own (`SMOLVLA_PROCESSOR_STEPS`, `PI05_PROCESSOR_STEPS`, and one per
   family after them). A step another type uses is refused with a sentence that lists what this
   type allows. A step no type may name, the action tokenizer first among them, is refused with
   a sentence of its own that says what is wrong with it (`REFUSED_STEPS`), before the one that
   only lists names.
2. **A tokenizer comes without its weights.** A model a step names is fetched as its configs and
   tokenizer files (`TOKENIZER_FILES`), and a model `config.json` names, which the policy is built
   from, keeps its safetensors (`NESTED_FILES`). A repository named both ways, as SmolVLA names
   its backbone, is fetched whole (`nested_patterns`). The pinned directory's own check is
   unchanged: anything but configs, tokenizer files and safetensors refuses the load.
3. **A family is served once its own code has been read.** Each type's processors, its loader,
   how it pads a missing camera and how it turns its answer into joint goals are read at the pin,
   and what its code does that the others' does not is checked as its code would read it,
   before any pin is checked or any weight fetched (`check_family`). What it keeps that the arm's
   frame check needs, where the normaliser's statistics would not have it, comes back with the
   checkpoint and is what `/v1/policy` reports (`Family`). Rok asked on 2026-10-25 for GR00T
   N1.7, pi0, pi0-FAST, X-VLA and MolmoAct2, and their reading decided the rest: pi0, X-VLA in its
   `auto` action mode and GR00T N1.7 with joint action groups are served, pi0-FAST is refused,
   and MolmoAct2 is deferred, at Rok's word.
4. **What is served, and what is not, is data in the core.** `quackd/adapters/policies.py`
   holds a row per served type, in the server's order, and a row per policy or service people
   ask about that quackd does not serve, with why and what would change that. It imports nothing
   of the arm's, as `catalogue.py` does not for the robots, so a page or a command can print it
   on a machine with no LeRobot, and a test ties its rows to the types the server serves.

**Where the code settled differently from the plan.**

- The plan had GR00T decode through grouped statistics alone. Half the SO-101 GR00T fine-tunes
  read on the day decode through their dataset's statistics instead
  (`groot_action_unpack_unnormalize_v2`), absolute joints too, so both are served, each refused
  when it has no statistics to unnormalise with.
- The plan pinned GR00T's base and took its backbone to be built from LeRobot's own config. It
  is, only while the base's `config.json` names `nvidia/Cosmos-Reason2-2B`, and any other
  backbone is fetched trusting its repository's code, so the pinned base's `config.json` is read
  before any weight and another backbone refused.
- The plan had every step checked for `checkpoint_path` and `discrete_action_tokenizer`, and a
  `checkpoint_revision` that disagrees with its pin refused. Those are MolmoAct2's keys alone,
  and MolmoAct2 is deferred, so they wait for it.
- Serving a pinned model offline found that `snapshot_download` asks the Hub for a commit's file
  list whatever `HF_HUB_OFFLINE` says, so the server now asks it for the cache's own files
  offline, and CI serves a tiny random X-VLA beside its tiny ACT.
- The plan left peft out. LeRobot's `[groot]` extra brings it, and with it installed
  transformers loads a
  PEFT adapter it finds in a model's directory onto that model, or, when the directory has no
  `config.json`, loads the base model the adapter names, which no pin fixes, so a pinned model
  whose directory holds an `adapter_config.json` is refused.
- **Amended 2026-10-26:** the server's extra brings diffusers and dm-tree, which a GR00T
  answers with, rather than LeRobot's `[groot]`, whose datasets, pyarrow, pandas, av,
  torchcodec, decord, peft and timm, about 340 MB, are for training
  (`GROOT_INFERENCE_PACKAGES`). A GR00T on a server without either is refused before a pin
  or a weight. The PEFT refusal stays, for a peft installed some other way.

## Why not

- **A vendor that serves many VLAs.** None existed on 2026-10-25, for the reasons above. A
  rented GPU running quackd's own server is what can be had, and the server is what decides
  what runs on it.
- **Every type LeRobot registers at once.** Each type's processors are code, and its loader, its
  padding and its decoding differ from the others': pi0's loader hands back a random network
  when its weights do not load, as pi05's does, X-VLA decides what its answer means by a mode
  in its config, and GR00T keeps the statistics the arm's frame check needs where LeRobot's
  normaliser does not. A type served unread is one whose refusals nobody wrote.
- **One allowlist for every type, with the new families' steps added to it.** It is the hole
  above, made wider by each family.
- **MolmoAct2 with an exemption for its code.** Its base model maps its classes to code of its
  own (`auto_map`), which quackd refuses in every model it loads. Rok's call is to defer it
  rather than write the first exemption.
- **pi0-FAST.** Its policy loads the FAST action tokenizer with `trust_remote_code=True`,
  hard-wired in its constructor, so no override quackd could pass would stop the tokenizer's own
  Python running in the server.

## Consequences

- **No trained checkpoint of the new families has run anywhere.** Their checks are written
  against stand-ins of the real ones' files, and none has been loaded, benched or served, on a
  laptop or on a GPU. CI serves a tiny random X-VLA, which proves the plumbing and nothing about
  what a trained one does.
- A pi05 fetches its tokenizer's 21.9 MB and not PaliGemma's 11.69 GB of weights.
- An ACT, a SmolVLA or a pi05 that names a step another type uses is refused where it was
  served. No checkpoint LeRobot's own processor factories write does that.
- A pinned model whose directory holds a PEFT adapter is refused where it was loaded without
  the adapter.
- [ADR-0048](0048-policies-are-the-arms-executor.md) carries a note pointing here, and its
  decision is extended, not reversed: the server still serves only what it has read.
