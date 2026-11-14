"""The learned policies quackd's policy server serves, and the ones it does not, as data.

`quackd_lerobot.policy` decides what is served: its server reads a checkpoint's own files and
refuses every type whose processors were not read at the LeRobot release it pins
(`upstream_api.SERVED_TYPES`, `pipeline.STEPS_BY_TYPE`). This file only says what each one is,
for the pages and the commands that print a table of them on a machine where neither LeRobot
nor the arm's package is installed, so it imports neither, and everything here is a string, as
`catalogue.py` is for the robots. `tests/test_upstream_api.py` ties `FAMILIES` to the types the
server serves, in its order, and every `NOT_SERVED` row to a type it does not.

What a row says about running is how far each one has got as this was written, and none of the
served ones has driven an arm: `ran` says which have run at all, and where.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PolicyFamily:
    """One LeRobot policy type quackd's server serves, said without importing it."""

    type: str
    """The `type` a checkpoint's `config.json` names it by."""
    label: str
    maker: str
    """Whose model it is: the people or the company behind it, and not who ported it."""
    takes_words: bool
    """Whether it is told the subtask in words, which is what `manipulate` hands it. An ACT
    is not: it learns one task from your demonstrations and is told nothing."""
    extra: str
    """What installs the server that serves it, on Python 3.12."""
    needs: tuple[str, ...]
    """What serving it takes besides: each model a checkpoint of it names inside itself, which
    `--pin REPO@REVISION` fixes, and each gated one a Hub token has to have been granted."""
    gpu: str
    """Where it answers a step in time, as far as anybody here knows."""
    licence: str
    """The licence of its base weights, as their own page said it."""
    licence_url: str | None
    """The page that licence was read on, or None where there are no base weights."""
    licence_read: str | None
    """The day that page was read."""
    ran: str
    """How far it has got with quackd."""


_EXTRA = "quackd[lerobot-vla]"

FAMILIES: tuple[PolicyFamily, ...] = (
    PolicyFamily(
        type="act",
        label="ACT",
        maker="Tony Z. Zhao (ALOHA)",
        takes_words=False,
        extra=_EXTRA,
        needs=(),
        gpu="no: it answered on a laptop's CPU",
        licence="yours: you train it on your own demonstrations",
        licence_url=None,
        licence_read=None,
        ran=(
            "a trained ACT from the Hub drove the arm's twin on the simulator from a laptop's "
            "CPU on 2026-09-28, and CI serves a tiny random one, but none has driven the arm"
        ),
    ),
    PolicyFamily(
        type="smolvla",
        label="SmolVLA",
        maker="Hugging Face",
        takes_words=True,
        extra=_EXTRA,
        needs=("--pin for the backbone its config names (vlm_model_name)",),
        gpu="wants one: it took minutes a chunk on a laptop's CPU",
        licence="Apache-2.0",
        licence_url="https://huggingface.co/lerobot/smolvla_base",
        licence_read="2026-11-14",
        ran=(
            "lerobot/smolvla_base loaded on a laptop's CPU on 2026-09-28 and never answered a "
            "step in time"
        ),
    ),
    PolicyFamily(
        type="pi05",
        label="Pi0.5",
        maker="Physical Intelligence",
        takes_words=True,
        extra=_EXTRA,
        needs=(
            "--pin google/paligemma-3b-pt-224@REVISION, its tokenizer",
            "a Hub token granted google/paligemma-3b-pt-224, which is gated",
        ),
        gpu="wants one (not measured)",
        licence="gemma on its card, Apache 2.0 on LeRobot's page",
        licence_url="https://huggingface.co/lerobot/pi05_base",
        licence_read="2026-11-14",
        ran="has not run",
    ),
    PolicyFamily(
        type="pi0",
        label="Pi0",
        maker="Physical Intelligence",
        takes_words=True,
        extra=_EXTRA,
        needs=(
            "--pin google/paligemma-3b-pt-224@REVISION, its tokenizer",
            "a Hub token granted google/paligemma-3b-pt-224, which is gated",
        ),
        gpu="wants one (not measured)",
        licence="gemma on its card, Apache 2.0 on LeRobot's page",
        licence_url="https://huggingface.co/lerobot/pi0_base",
        licence_read="2026-11-14",
        ran="has not run",
    ),
    PolicyFamily(
        type="xvla",
        label="X-VLA",
        maker="Jinliang Zheng et al. (2toINF)",
        takes_words=True,
        extra=_EXTRA,
        needs=("--pin facebook/bart-large@REVISION, its tokenizer",),
        gpu="wants one (not measured)",
        licence="Apache-2.0",
        licence_url="https://huggingface.co/lerobot/xvla-base",
        licence_read="2026-11-14",
        ran=(
            "CI serves a tiny random one, and no trained one has run, and only its auto action "
            "mode is served"
        ),
    ),
    PolicyFamily(
        type="groot",
        label="GR00T N1.7",
        maker="NVIDIA",
        takes_words=True,
        extra=_EXTRA,
        needs=(
            "--pin nvidia/GR00T-N1.7-3B@REVISION, the base its config names (base_model_path)",
            "--pin nvidia/Cosmos-Reason2-2B@REVISION, its backbone's tokenizer",
            "a Hub token granted nvidia/Cosmos-Reason2-2B, which is gated",
        ),
        gpu="wants one (not measured)",
        licence="NVIDIA Open Model License",
        licence_url="https://huggingface.co/nvidia/GR00T-N1.7-3B",
        licence_read="2026-11-14",
        ran="has not run, and only checkpoints whose every action group is joints are served",
    ),
)
"""The types the server serves, in `upstream_api.SERVED_TYPES`' order."""


@dataclass(frozen=True)
class NotServed:
    """A policy, a family or a hosted service people ask about that quackd does not serve, why
    not, and what would change that."""

    name: str
    maker: str
    type: str | None
    """The LeRobot `type` it would be served as, or None for one that is not a LeRobot type."""
    why: str
    would_change: str


NOT_SERVED: tuple[NotServed, ...] = (
    NotServed(
        name="FLUX 3 Action",
        maker="Black Forest Labs",
        type="flux3",
        why=(
            "it is a LeRobot policy type since 0.7.0 that quackd has not read, and it needs Linux, "
            "an NVIDIA GPU with about 32 GB in BF16 by its makers' report, and a NATTEN built "
            "for that machine, which publishes no wheel yet for the torch 2.15 LeRobot 0.7.0 "
            "installs; its weights are gated under the FLUX Kommunity License"
        ),
        would_change=(
            "a spike on a rented Linux GPU showing that `quackd policy check --bench` holds its "
            "rate, that its delta actions stay anchored when a goal is clipped, and that the "
            "official SO-101 checkpoint drives the simulator end to end"
        ),
    ),
    NotServed(
        name="Pi0-FAST",
        maker="Physical Intelligence",
        type="pi0_fast",
        why=(
            "its policy loads the FAST action tokenizer with trust_remote_code=True, hard-wired, "
            "so serving it runs Python code the tokenizer's repository brings with it"
        ),
        would_change="LeRobot loading the FAST tokenizer without the repository's own code",
    ),
    NotServed(
        name="MolmoAct2",
        maker="Ai2",
        type="molmoact2",
        why=(
            "its base model maps its classes to code of its own (auto_map), which quackd refuses "
            "in every model it loads"
        ),
        would_change=(
            "a decision to accept the auto_map of a checkpoint whose LeRobot loader ignores it, as "
            "LeRobot 0.7.0's MolmoAct2 loader does, fetching none of the repository's Python and "
            "building LeRobot's own copy of its code, which quackd would read first"
        ),
    ),
    NotServed(
        name="OpenVLA",
        maker="the OpenVLA team (Stanford and partners)",
        type=None,
        why=(
            "it is not a LeRobot policy type at 0.7.0, and it loads through transformers with "
            "its repository's own code (auto_map)"
        ),
        would_change="a LeRobot port of it",
    ),
    NotServed(
        name="the Pi API",
        maker="Physical Intelligence",
        type=None,
        why=(
            "it is a hosted beta, from 2026-10-13, behind a waitlist, with no public protocol "
            "to speak"
        ),
        would_change="a public protocol, and access to it",
    ),
    NotServed(
        name="the Gemini Robotics VLA",
        maker="Google DeepMind",
        type=None,
        why=(
            "it is offered to partners only: Gemini Robotics 2.5, its newest, has been rolling out "
            "to robotics partners since 2026-11-10, with a waitlist for early access. Gemini "
            "Robotics-ER 2, which is public, is a planner, and quackd already carries it as a "
            "pilot row (gemini-robotics-er-2-preview), which nothing on record has flown"
        ),
        would_change="public access to the VLA itself",
    ),
    NotServed(
        name="GR00T 2",
        maker="NVIDIA",
        type=None,
        why=(
            "it was announced on 2026-11-09 with no weights to fetch: NVIDIA's page says its code "
            "comes in December, no licence has been named for it, and it is no LeRobot policy "
            "type at 0.7.0"
        ),
        would_change=(
            "NVIDIA publishing its weights under a licence that allows it, a LeRobot port in a "
            "release quackd pins, and quackd reading that port's processors there; GR00T N1.7 is "
            "the GR00T quackd serves until then"
        ),
    ),
    NotServed(
        name="Praxis-1",
        maker="Runway",
        type="praxis",
        why=(
            "its open weights came on 2026-11-11, and it is a LeRobot policy type only on "
            "LeRobot's main branch since that day, in no release quackd can pin; it needs one GPU "
            "with 80 GB by LeRobot's own page, and its SO-101 checkpoint is in Runway's own "
            "format, not LeRobot's"
        ),
        would_change=(
            "a LeRobot release that carries it, quackd's pin moving to that release and reading "
            "its processors there, and an SO-101 checkpoint in LeRobot's format"
        ),
    ),
    NotServed(
        name="the Cosmos 3 policies",
        maker="NVIDIA",
        type="cosmos3_edge",
        why=(
            "Cosmos3 Edge and Nano (`cosmos3_edge`, `cosmos3_nano`) are LeRobot policy types "
            "since 0.7.0 that quackd has not read, and LeRobot's own page says the base "
            "checkpoints' action modules are not trained for a robot yet"
        ),
        would_change=(
            "quackd reading their processors at the pin, and a checkpoint of them trained on an arm"
        ),
    ),
    NotServed(
        name="vLLM-Omni and the OpenPI wire",
        maker="the vLLM project",
        type=None,
        why=(
            "it serves several VLAs over OpenPI's WebSocket protocol, but it is a server you run "
            "and not a vendor, and its wire is not the checked JSON quackd's client speaks"
        ),
        would_change="a client for that wire that keeps every check quackd's own protocol makes",
    ),
    NotServed(
        name="Isaac-GR00T N1.6 checkpoints",
        maker="NVIDIA",
        type=None,
        why=(
            "they are in Isaac-GR00T's own format and not LeRobot checkpoints, and LeRobot 0.7.0 "
            "serves GR00T N1.7 alone"
        ),
        would_change="LeRobot reading them, or the same task fine-tuned on N1.7 through LeRobot",
    ),
)
"""What quackd does not serve, each with why and what would change that."""

BY_TYPE: dict[str, PolicyFamily] = {family.type: family for family in FAMILIES}
