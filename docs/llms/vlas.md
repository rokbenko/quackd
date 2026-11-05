# VLAs and the other learned policies

A learned policy is the third model a run can have, and only the SO-101 arm takes one. When the
pilot calls `manipulate` or `pick`, the arm is handed to the policy for one short segment: the
policy reads the arm and its cameras many times a second and answers with a goal for every
motor, and quackd paces those goals on the arm's clock and holds each one to the arm's rules
before it is sent. A VLA, a vision-language-action model, is a policy told the subtask in words,
which is what `manipulate` hands it. ACT is a policy that is told nothing: it learns one task
from your own demonstrations.

> [!WARNING]
> **No learned policy has driven a real arm through quackd.** As of 2026-10-25 every segment has
> run against the test suite's fake arm and on the arm's simulator. A trained ACT from the Hub
> drove a twin of the lab's arm on 2026-09-28 and 2026-09-29, SmolVLA loaded and never answered a
> step in time on a laptop's CPU, and no other family has run at all. Rehearse on the simulator
> first, and keep a hand on the arm's power switch the first time a policy drives it.

## Why Hugging Face

There is no vendor that serves many VLAs per call, the way OpenRouter serves many language
models behind one key. On 2026-10-25 Hugging Face's serverless Inference Providers served no
robotics model at all, NVIDIA hosted no inference API for GR00T, Physical Intelligence's Pi API
was a beta behind a waitlist with no public protocol, and the Gemini Robotics VLA was offered to
partners only. What does play that part for open VLAs is Hugging Face's LeRobot and its Hub: one
library and one checkpoint format for many policy families, NVIDIA's GR00T and Physical
Intelligence's pi0 among them, and hundreds of fine-tunes for the SO-101 on the Hub. quackd's
policy server is LeRobot behind quackd's own checks, so a family it has read is served from any
Hub repository at a revision, and a Hugging Face Inference Endpoint can run that server on a GPU
Hugging Face rents by the hour. Why it is shaped this way is
[ADR-0060](../adr/0060-hugging-face-serves-many-vlas.md) and
[ADR-0061](../adr/0061-a-hugging-face-endpoint-is-a-rented-gpu-quackd-starts.md).

## The families quackd serves

<!-- generated: policy-served, by scripts/models_docs.py from quackd/adapters/policies.py. Change a row there and run the script, not this table. -->

| Type | Family | Maker | Told the subtask in words | Needs besides the extra | GPU | Licence | How far it has got |
|---|---|---|---|---|---|---|---|
| `act` | ACT | Tony Z. Zhao (ALOHA) | no, it learns one task | nothing | no: it answered on a laptop's CPU | yours: you train it on your own demonstrations | a trained ACT from the Hub drove the arm's twin on the simulator from a laptop's CPU on 2026-09-28, and CI serves a tiny random one, but none has driven the arm |
| `smolvla` | SmolVLA | Hugging Face | yes | --pin for the backbone its config names (`vlm_model_name`) | wants one: it took minutes a chunk on a laptop's CPU | [Apache-2.0](https://huggingface.co/lerobot/smolvla_base), read 2026-10-25 | lerobot/smolvla_base loaded on a laptop's CPU on 2026-09-28 and never answered a step in time |
| `pi05` | Pi0.5 | Physical Intelligence | yes | `--pin google/paligemma-3b-pt-224@REVISION`, its tokenizer; a Hub token granted google/paligemma-3b-pt-224, which is gated | wants one (not measured) | [gemma on its card, Apache 2.0 on LeRobot's page](https://huggingface.co/lerobot/pi05_base), read 2026-10-25 | has not run |
| `pi0` | Pi0 | Physical Intelligence | yes | `--pin google/paligemma-3b-pt-224@REVISION`, its tokenizer; a Hub token granted google/paligemma-3b-pt-224, which is gated | wants one (not measured) | [gemma on its card, Apache 2.0 on LeRobot's page](https://huggingface.co/lerobot/pi0_base), read 2026-10-25 | has not run |
| `xvla` | X-VLA | Jinliang Zheng et al. (2toINF) | yes | `--pin facebook/bart-large@REVISION`, its tokenizer | wants one (not measured) | [Apache-2.0](https://huggingface.co/lerobot/xvla-base), read 2026-10-25 | CI serves a tiny random one, and no trained one has run, and only its auto action mode is served |
| `groot` | GR00T N1.7 | NVIDIA | yes | `--pin nvidia/GR00T-N1.7-3B@REVISION`, the base its config names (`base_model_path`); `--pin nvidia/Cosmos-Reason2-2B@REVISION`, its backbone's tokenizer; a Hub token granted nvidia/Cosmos-Reason2-2B, which is gated | wants one (not measured) | [NVIDIA Open Model License](https://huggingface.co/nvidia/GR00T-N1.7-3B), read 2026-10-25 | has not run, and only checkpoints whose every action group is joints are served |

<!-- end generated: policy-served -->

Each is a LeRobot policy type, read at LeRobot 0.6.1 before quackd serves it: its processors,
its loader, how it pads a camera it is not given and how it turns its answer into goals for the
arm's joints. A checkpoint of any other type is refused before a byte of its weights is fetched,
and so is one of these types whose own settings would not answer in the arm's joints, such as an
X-VLA in an end-effector mode or a GR00T that decodes an end effector's pose. No quackd command
loads a checkpoint in the process that owns the arm's serial bus: the server runs in a process of
its own, and the arm checks at connect that the policy fits it before any motor is energised.
What each family needs, what it refuses and why, and its licence in full are in
[guides/policies.md](../guides/policies.md#the-policies).

## Using one, end to end

1. **Find a checkpoint trained on your arm.** A policy learns the calibration and the cameras of
   the arm it was trained on. One trained on another calibration is refused at connect unless
   `--accept-other-frame` says the frames match, and its goals are clipped to your arm's travel
   either way. Record with LeRobot and push the checkpoint to a Hub repository of your own, or
   start from a fine-tune somebody has published.
2. **Check it, then bench it:** `quackd policy check --policy OWNER/NAME@REVISION --bench` reads
   its files before any weight is fetched, says what it needs, and times it through the real
   client.
3. **Serve it** where it answers in time, below.
4. **Point a run at it:** `quackd run task.duck --robot arm-01-sim --policy-url ...`, on the arm's
   simulator first.

## Where it runs

| Where | Start it with | Reach it with | What it costs | How far it has got |
|---|---|---|---|---|
| The laptop beside the arm | `quackd policy serve --policy OWNER/NAME@REVISION` | `--policy-url http://127.0.0.1:9875` | nothing | an ACT drove the arm's twin from a laptop's CPU |
| A GPU you rent anywhere | the same command there, and `ssh -L 9875:127.0.0.1:9875` from the laptop | `--policy-url http://127.0.0.1:9875` | that GPU's rate | no round trip through one has been timed |
| A Hugging Face Inference Endpoint | `quackd policy endpoint up NAME --policy OWNER/NAME@REVISION` | `--policy-url hf:NAME` | from $0.80 an hour on an L4 in us-east-1, Hugging Face's price on 2026-10-25, and nothing while paused | quackd has never created one: it has run against stand-ins only |

The endpoint is the closest thing there is to a vendor serving the policy for you: one command
starts quackd's own server on a GPU Hugging Face runs, asks before it bills, waits until it
serves and pauses it if it fails, and `quackd policy endpoint pause NAME` stops the bill. Your
Hugging Face token goes to Hugging Face's own API and to the endpoint's own address and nowhere
else, and the server's own token goes to Hugging Face once, as the endpoint's secret, and
otherwise stays on this machine. How each is set up is in
[guides/policies.md](../guides/policies.md#on-a-hugging-face-endpoint).

## Not served, and why

<!-- generated: policy-refused, by scripts/models_docs.py from quackd/adapters/policies.py. Change a row there and run the script, not this table. -->

| Not served | Maker | LeRobot type | Why not | What would change that |
|---|---|---|---|---|
| FLUX 3 Action | Black Forest Labs | `flux3` | it is on LeRobot's main branch only, after 0.6.1, and needs Linux, an NVIDIA GPU with about 32 GB in BF16 by its makers' report, and a NATTEN built for that machine | a spike on a rented Linux GPU showing that `quackd policy check --bench` holds its rate, that its delta actions stay anchored when a goal is clipped, and that the official SO-101 checkpoint drives the simulator end to end |
| Pi0-FAST | Physical Intelligence | `pi0_fast` | its policy loads the FAST action tokenizer with trust_remote_code=True, hard-wired, so serving it runs Python code the tokenizer's repository brings with it | LeRobot loading the FAST tokenizer without the repository's own code |
| MolmoAct2 | Ai2 | `molmoact2` | its base model maps its classes to code of its own (auto_map), which quackd refuses in every model it loads | a decision to read that code and exempt it, or a port whose model loads with code transformers already has |
| OpenVLA | the OpenVLA team (Stanford and partners) | none | it is not a LeRobot policy type at 0.6.1, and it loads through transformers with its repository's own code (auto_map) | a LeRobot port of it |
| the Pi API | Physical Intelligence | none | it is a hosted beta, from 2026-10-13, behind a waitlist, with no public protocol to speak | a public protocol, and access to it |
| the Gemini Robotics VLA | Google DeepMind | none | it is offered to partners only. Gemini Robotics-ER 2, which is public, is a planner, and quackd already carries it as a pilot row (gemini-robotics-er-2-preview), which nothing on record has flown | public access to the VLA itself |
| vLLM-Omni and the OpenPI wire | the vLLM project | none | it serves several VLAs over OpenPI's WebSocket protocol, but it is a server you run and not a vendor, and its wire is not the checked JSON quackd's client speaks | a client for that wire that keeps every check quackd's own protocol makes |
| Isaac-GR00T N1.6 checkpoints | NVIDIA | none | they are in Isaac-GR00T's own format and not LeRobot checkpoints, and LeRobot 0.6.1 serves GR00T N1.7 alone | LeRobot reading them, or the same task fine-tuned on N1.7 through LeRobot |

<!-- end generated: policy-refused -->

## What has run, and what has not

A tiny random ACT and a tiny random X-VLA run through the real server and client in CI, offline.
A trained ACT from the Hub, `natsuki0000/act-so101-bluecap`, drove a twin of the lab's arm on
the simulator from a laptop's CPU on 2026-09-28 over `quackd serve-mcp`, and on 2026-09-29 under
OpenAI's `gpt-6-sol` in a `quackd run --goal`. `lerobot/smolvla_base` loaded on that laptop and
took minutes a chunk. No trained pi05, pi0, X-VLA or GR00T checkpoint has been loaded, benched
or served, no round trip through a rented GPU has been timed, no Hugging Face Endpoint has been
created, and no policy has driven the real arm. The run that would settle the arm's part is an
item in [PLAN.md](../../PLAN.md).
