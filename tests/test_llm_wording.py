"""Text that means an LLM says LLM: the owner's rule, for everything here that somebody reads.

`tests/test_example_logs.py` holds the run logs to it, and this holds the rest of the repository.
What a page quotes is left out the way that test leaves it out: a fenced block, a code span and a
link target are real output and names. Python is read through its tokenizer, so a name is no
sentence.

The word still appears where it means something else: a simulator's, a policy's, a detector's, a
board's, a pydantic class, a proper name. Each of those is listed below with its reason, so a new
use fails here until somebody says why it may stay. Adding a sentence to `SENTENCES` is that saying.
"""

from __future__ import annotations

import io
import keyword
import re
import subprocess
import tokenize
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

NOT_THE_WORD = re.compile(r"\b(?:AI\s+)?models?\b", re.IGNORECASE)
"""The same pattern `scripts/example_log.py` holds a log to."""

READ = re.compile(r".*\.(md|py|duck|toml|yml|html|js|css)|\.env\.example")
"""Which files say something. A lock file, a recording and a generated schema do not."""

WHOLE_FILES: dict[str, str] = {
    r"CHANGELOG\.md": "a released section keeps what it said, [Unreleased] quotes what changed",
    r"docs/examples/lerobot/e\d+/(README\.md|log/.*)": "a run log keeps what its run said",
    r"tests/test_example_logs\.py": "proves the run logs' guard refuses the word",
    r"tests/test_llm_wording\.py": "this file",
    r"scripts/example_log\.py": "holds the pattern, and reads the key an older run wrote",
    # the simulator
    r"adapters/lerobot/src/quackd_lerobot/sim/.*": "the MuJoCo model of the arm",
    r"adapters/microduck/src/quackd_microduck/sim3d/.*": "the MuJoCo model of the duck",
    r"adapters/toddlerbot/.*|bridge/toddlerbot/.*": "upstream's MuJoCo model of the ToddlerBot",
    r"docs/assets/(lerobot_sim|hero3d|lerobot_real)\.py": "scripts that render a simulator",
    r"web/src/(microduck|view)\.js": "the MuJoCo model, in the browser",
    r"docs/adr/0030-.*|docs/adr/0047-.*": "ADRs about the simulators",
    # policies
    r"adapters/lerobot/src/quackd_lerobot/policy/.*": "learned policies",
    r"quackd/adapters/policies\.py": "learned policies",
    r"docs/guides/policies\.md": "learned policies",
    r"docs/adr/006[01]-.*": "ADRs about learned policies",
    # detectors, the radio and boards
    r"bridge/jetson/.*": "the daemon's detector",
    r"quackd/perception/.*": "a detector's model",
    r"quackd/radio/.*": "the radio's log-distance model",
    r"quackd/boards/.*": "a computer's model",
    # somebody else's words
    r"NOTICE": "licences",
    r"docs/reference/licenses\.md": "licences",
    r"docs/passport/prior-art\.md": "what other standards call a robot's model",
    # tests that have to spell it, which are the only tests that may
    r"tests/test_(lerobot_sim.*|sim3d.*|robot_twin|acceptance_.*|preflight)\.py": "a simulator",
    r"tests/test_(extras_absent|toddlerbot_.*|upstream_api|llm_in_the_simulator)\.py": "the sim",
    r"tests/test_policy_.*\.py|tests/fake_hugging_face\.py": "learned policies",
    r"tests/test_(jetson_.*|host_.*|perception_.*|detector_selection)\.py": "a detector",
    r"tests/fake_jetson_hostd\.py": "a detector",
    r"tests/test_(boards.*|passport_.*|onboard_.*|radio_.*|lan)\.py": "a board, the radio",
    r"tests/linux_board_fixtures\.py": "a board",
    r"tests/test_(lerobot_adapter|alohamini_.*|open_duck_.*|datasheets|adapters)\.py": "a servo",
    r"tests/fake_alohamini_host\.py": "a robot's model",
    r"tests/test_(openai_headers.*|openrouter_wire|providers|local_provider)\.py": "a vendor's",
    r"tests/test_(decisions_api.*|web|doctor|decision_llms|catalogue)\.py": "a vendor's",
    r"tests/test_(cli|command|docs|live_decision|pypi_readme)\.py": "a vendor's, or pinned words",
    r"tests/fake_(openai_.*|openrouter|decisions_api|systemone|laya)\.py": "stands in for one",
}

ANYWHERE: dict[str, str] = {
    r"model[- ]context[- ]protocol": "a proper name",
    r"meta model api": "a proper name",
    r"(?:alibaba )?(?:cloud )?model studio": "a proper name",
    r"model vault": "a proper name",
    r"model parameter reference": "a proper name",
    r"open model licen[cs]e": "a proper name",
    r"model cards?\b": "what Hugging Face calls one",
    r"threat model": "no LLM",
    r"trust model": "no LLM",
    r"data model": "no LLM",
    r"pydantic (?:params )?models?": "no LLM",
    r"parameter model": "a pydantic class",
    r"params model": "a pydantic class",
    r"message model": "a pydantic class",
    r"physics model": "the simulator",
    r"mujoco model": "the simulator",
    r"3d model": "the simulator",
    r"maker's (?:own )?model": "the simulator",
    r"upstream's (?:own )?(?:cc by-nc-sa )?(?:microduck )?model": "the simulator",
    r"so-101's (?:mujoco )?model": "the simulator",
    r"so-arm100(?:'s)? model": "the simulator",
    r"microduck(?:'s own)? model": "the simulator",
    r"the model and (?:the |its )?(?:policies|policy|meshes|every mesh)": "the simulator",
    r"log-distance model|textbook model": "the radio",
    r"actuator model": "a servo",
    r"model numbers?": "a servo",
    r"yolo model": "a detector",
    r"vision-language-action model": "a learned policy",
    r"base model": "a learned policy",
    r"/proc/device-tree/model": "a computer's name for itself",
    r"vendor, model\b|maker, model\b|maker and model\b": "a robot's identity",
}
ANYWHERE_RE = [re.compile(pattern, re.IGNORECASE) for pattern in ANYWHERE]

SENTENCES: dict[str, tuple[str, ...]] = {
    ".github/workflows/lerobot-sim-assets.yml": (
        "The SO-101's real model, fetched",
        "# simulator's real-model sweeps run",
        "on the real model, ten",
    ),
    ".github/workflows/microduck-assets.yml": (
        "The Microduck's real model and its",
        "name: fetch the model, then",
    ),
    ".github/workflows/toddlerbot-contract.yml": ("path imports, the model, its",),
    "CONTRIBUTING.md": (
        "marked , the model check in",
        "job fetches the model and runs",
    ),
    "PLAN.md": (
        "against the model's stop,",
        "fold puts the model's fingers",
        "says whether the model's frame",
        "table: in the model, the",
        "model's frame",
        "refuses in every model it loads,",
    ),
    "README.md": (
        "render of Pollen's model under its",
        "shipped. On that model a grasp",
        "that fetches the model for those",
        "stand are the model's and",
        "nightly because the model is fetched",
        "per frame, no model download. Bearing",
        "names and any model it names",
        ", with its model, and",
        "robot and which model it is.",
        "it is a model followed rather",
        "the arm's, whose model is fetched",
        "10 MB of model and policy",
        "16 MB of model into the",
        "connect loads the model and renders",
        "backend over a model of it,",
        "placement are the model's and",
        "by standing the model up, because",
        "machine with the model's own",
        "which renders that model and carries",
        "fetches the Microduck's model and its",
        "yet?] renders that model, so",
        "it carries the model's own",
    ),
    "RELEASING.md": (
        "schema to its model;",
        "simulator and the model it fetches,",
    ),
    "SECURITY.md": (
        "- **The model the arm's",
        "model from TheRobotStudio/SO-ARM100",
        "the cache. The model includes no",
        "model). It",
        "and refuses a model the checkpoint",
        "code trusted, a model a checkpoint",
    ),
    "adapters/alohamini/src/quackd_alohamini/sim2d.py": ("shared simulator already models: it",),
    "adapters/alohamini/src/quackd_alohamini/upstream_api.py": (
        "quackd derives the model from the",
    ),
    "adapters/alohamini/src/quackd_alohamini/verbs.py": ("the two 6dof models have",),
    "adapters/huebot/src/quackd_huebot/__init__.py": ("whose model and gait",),
    "adapters/lerobot/README.md": (
        "model from TheRobotStudio's",
        "reads from the model is in",
    ),
    "adapters/lerobot/src/quackd_lerobot/__init__.py": (
        "the arm's simulator model, which",
        "and whether that model is in",
        "one without the model is refused,",
        "physics and the model",
    ),
    "adapters/lerobot/src/quackd_lerobot/real.py": (
        "answering as another model",
        "(expected model: <M>)up.HANDSHAKE_NAMES_THE_ID`),",
        "answered as another model, is",
        "ping and a model check per",
    ),
    "adapters/lerobot/src/quackd_lerobot/upstream_api.py": (
        '"src/lerobot/model/kinematics.py"',
        "simulator scales its model gripper's force",
        "reading on a model of the",
        'name -> Motor(id, model, norm_mode)"',
        "- <N> (expected model: <M>)'",
        '"another model, each',
    ),
    "adapters/microduck/src/quackd_microduck/__init__.py": ("# protocol, the model and walking",),
    "adapters/microduck/src/quackd_microduck/transports/mujoco.py": (
        "is upstream's real model on upstream's",
        "# of upstream model on first",
    ),
    "adapters/microduck/src/quackd_microduck/upstream_api.py": (
        '"(DEFAULT_POSITION, duck-control/src/model.rs:37-53), so',
        "word on its model, and",
    ),
    "adapters/rosbridge/src/quackd_rosbridge/upstream_api.py": ("had one) or model a tool",),
    "docs/adapters/alohamini/README.md": ("and motors per model |",),
    "docs/adapters/huebot/README.md": ("Microduck's  and  backends model the Microduck's",),
    "docs/adapters/lerobot/README.md": (
        "(read from the model and its",
        "assumptions about the model, and",
        "second fetches the model",
        "it, in a model: [the",
        "model a checkpoint",
        "revision, once per model. That",
        "revision. A pinned model a processor",
        "pinned model whose directory",
        "adapter onto the model, or,",
        "and the pinned models, and",
        "into the model and none",
        "connect fetches the model, the",
        "limited by the model's own",
        "stops against the model's yet.",
        "also put the model into its",
        "model's stops,",
        "can put the model into its",
        "bench, so the model differs from",
        "model can start",
        "physics is the model's rather",
        "five of the model's physics",
        "the table, the model's stops",
        "happens is the model's physics,",
        "settles is the model's answer",
        "dynamics are the model's, a",
        "evidence about the model (, below).",
        "to match the model until a",
        "pose puts the model into its",
        "rest pose the model can start",
        "at:  records the model's zero,",
        "parks it, the model is in",
        "somewhere without the model | point",
        "fetch the pinned model |",
        "answered as another model. LeRobot's",
        "simulator gives its model gripper half of that model's force",
        "reading on a model of the",
        "answered as another model: one",
        "is where that model comes from. The model is",
        "on fetches the model. The",
        "The model is the",
        "because a newer model is what",
        "for, and the model is then",
        "endings in the model do not",
        "|  | the model quackd loads:",
        "from the loaded model rather than",
        "every mesh the model names and",
        "vertices when the model loads |",
        "properties in the model came from:",
        "|  | the model's gripper",
        "dynamics as the model's and",
        "evidence about the model, and",
        "travel is the model's zero,",
        "stop against the model's. The",
        "degree turns the model's joint",
        "calibrated, which the model cannot know.",
        "found from the model |",
        "linearly over the model's gripper",
        "mount as the model places it",
        "logged and the model keeps what",
        "are not its model's |",
        "so a pinned model whose files",
        "step handing the model the domain",
        "one of the model's own.",
        "is the one model it names",
        "only once the model is built",
        "hands back the model as it",
        "step, the nested models pinned with",
        "with weights its model does not",
        "repeat. On that model a grasp",
    ),
    "docs/adapters/lerobot/first-run.md": (
        "everything in a model of it:",
        "fact about the model. The",
        "it, in a model:",
        "is on a model of the",
        "one of the model's",
        "it puts the model into its",
        "settles is the model's physics,",
        "physics is the model's, not",
        "match the model, the",
        "and past its model's stop",
    ),
    "docs/adapters/lerobot/hardware-checklist.md": (
        "fact about the model. If",
        "shape in a model and says",
        "than about the model.",
    ),
    "docs/adapters/microduck/README.md": (
        "one, and the model",
        "update to the model cannot make",
        "there, so the model's commit",
        "one toppled the model, so",
    ),
    "docs/adapters/microduck/hardware-checklist.md": ("by resetting the model. The",),
    "docs/adapters/status.md": (
        "commit. On that model a grasp",
        "job fetches the model and runs",
    ),
    "docs/adapters/writing-an-adapter.md": (
        "because the manifest model and the",
        "enforced by the model itself",
        "Both fetch their model from",
        "meant for a model never opens",
    ),
    "docs/adr/0001-language.md": ("params, transport models),  +",),
    "docs/adr/0005-duck-spec-v0.md": ("model () and",),
    "docs/adr/0008-perception-features-not-frames.md": ("whose model runs on",),
    "docs/adr/0016-flock-lockstep-clock.md": ("the honest hardware-shaped model and may",),
    "docs/adr/0025-memory-between-runs.md": ("store (an embedding model in",),
    "docs/adr/0027-alohamini.md": ("faithful enough to model the watchdog.",),
    "docs/adr/0031-llm-catalogue.md": (
        ", because Anthropic's models overview now",
        "price, because OpenAI's models page lists",
        "grokgrok-4.7`, which xAI's model chooser names",
    ),
    "docs/adr/0036-what-the-arm-does-not-say.md": ("that are the model's ().",),
    "docs/adr/0041-the-record-says-when-it-ran-and-what-it-cost.md": (
        "down on the model and every",
    ),
    "docs/adr/0045-a-rest-pose-the-calibration-cannot-reach.md": (
        "So wherever the model's own",
        "fold past the model's own",
        "stops against the model's. Nothing",
    ),
    "docs/adr/0046-the-jetson-is-reached-not-run-on.md": (
        "with its address, model and device,",
        ", the board's model, the",
    ),
    "docs/adr/0048-policies-are-the-arms-executor.md": (
        "steps, and a model a processor",
        "nothing in the model bounds joint",
        "not to. A model the checkpoint",
        "Users covers the model's Outputs",
    ),
    "docs/adr/0054-a-reflex-may-look-through-a-decision-llm.md": (
        "While a voice model talks,",
        "the demo's voice model talked while",
    ),
    "docs/adr/0057-a-robot-can-introduce-itself-to-one-it-does-not-know.md": (
        "not to its model. Pins",
    ),
    "docs/adr/0074-a-computer-quackd-cannot-recognise-is-named-and-says-what-it-calls-itself.md": (
        "that names no model. The",
    ),
    "docs/assets/README.md": ("upstream model:  renders TheRobotStudio's model of the",),
    "docs/concepts/architecture.md": (
        "scene around the model, maps",
        "through: the manifest's model, body,",
        ", its detector's model and device",
        "spent in the model ( and",
    ),
    "docs/concepts/learned-verbs.md": ("built from the model's own",),
    "docs/concepts/safety.md": ("dynamics are the model's and",),
    "docs/design/memory.md": ("means an embedding model in the",),
    "docs/design/robot-passport-phase-7.md": ("own Agent Card model parsing a",),
    "docs/faq.md": (
        "model in MuJoCo,",
        "dynamics are the model's, which",
        "because the model files are",
        "it. Under the model's own",
        "switch on the model training toggle",
    ),
    "docs/guides/decision-llms/README.md": (
        "charged | [their models page] ([jev])",
        "([models], re-read",
        "takes none ([models]), except",
    ),
    "docs/guides/decision-llms/jev.md": ("not charged ([their models page], read",),
    "docs/guides/decision-llms/kev.md": (
        "- **The model field is",
        "| Model field and",
        "at 's request model, echoed",
    ),
    "docs/guides/decision-llms/openai.md": (
        'and  *"the only model currently available"*',
        "*\"using the selected model's image",
    ),
    "docs/guides/decision-llms/opendecision.md": (
        'README: "Treat the model scores as',
        "uncalibrated. Evaluate the model and thresholds",
    ),
    "docs/guides/jetson.md": ("spent in the model",),
    "docs/guides/mcp.md": ("10 MB of model with nothing",),
    "docs/guides/openrouter.md": (
        "the model training toggle",
        "accepted on any model ID at",
        'the newest concrete model", and',
    ),
    "docs/guides/registry.md": ("it downloads a model, which",),
    "docs/guides/vlas.md": (
        "robotics model at all,",
        "refuses in every model it loads",
        "a port whose model loads with",
    ),
    "docs/llms/pilot-llms.md": ("Gemini Robotics model that does",),
    "docs/llms/vendors.md": ("the NVIDIA Open Model",),
    "docs/passport/compatibility.md": ("model, a",),
    "docs/passport/naming.md": ("RCP, after the Model Context",),
    "docs/passport/why.md": ("and is a model to follow",),
    "docs/reference/manifest-spec.md": (
        "The model is core",
        "## Invariants the model enforces",
    ),
    "pyproject.toml": ("3.12 floor. The model is",),
    "quackd/adapters/catalogue.py": ('"model, never',),
    "quackd/adapters/export.py": ("regenerates  from the model.",),
    "quackd/adapters/passport.py": ("than to its model, comes",),
    "quackd/agent/decision/catalogue.py": (
        "([their LLMs page](https://docs.typesafe.ai/models), read",
    ),
    "quackd/agent/loop.py": (
        "and its detector's model and device.",
        "bytes`, a model's out-of-memory",
    ),
    "quackd/agent/prompts.py": (
        "# simulator's collision model where there",
        "dynamics are the model's, which",
        '"view of the model, not',
        "simulator (MuJoCo): a model of this",
        "falls is the model's \"",
    ),
    "quackd/agent/providers/anthropic.py": ('supported on this model"',),
    "quackd/agent/providers/catalogue.py": (
        "embedding and OCR models are not",
        "supported for this model.Falseauto`",
        "# platform.claude.com models overview and model-deprecations. Claude",
        "id, but the models",
        "# developers.openai.com models and deprecations.",
        "# ai.google.dev models and deprecations.",
        "to the 2.5 models to users",
        '"our legacy Flash model" and',
        '# Flash model", and',
        '# earlier Flash model" and',
        '"our legacy Flash model" to',
        'supported with this model", and',
        'LLMs, under "Legacy models"; 3.7',
        'them under "Recommended models", though',
        "overview's own Latest Models are",
    ),
    "quackd/agent/providers/factory.py": (
        "LLM from /v1/models when none",
        "#: OpenRouter's model-variants page",
    ),
    "quackd/agent/providers/fake.py": ("come from whatever model is loaded,",),
    "quackd/agent/providers/openai.py": ("this model. Supported",),
    "quackd/agent/providers/openrouter.py": (
        "builds its own models for the",
        '"/models"',
        '"openrouter-models"',
        "build its own models for keeps",
    ),
    "quackd/agent/providers/pricing.py": ("rather than the model, because",),
    "quackd/cli.py": (
        'entry of /v1/models."',
        '"REPO@REVISION of a model the checkpoint',
        "moves. Once per model. A",
        "same to its model, because",
        "settle under the model's gravity,",
        "settled where the model's \"",
    ),
    "quackd/doctor.py": (
        "its model, and",
        "/models",
        '"answered /models with something',
        "# model is in",
        "(GET /v1/models, 1.5",
        "servers (GET /v1/models, 1.5",
    ),
    "quackd/duckfile/export.py": ("drifts from the model.",),
    "quackd/duckfile/schema.py": (
        "executor trusts this model and nothing",
        "cap is a model validator",
    ),
    "quackd/flock/pilots.py": ("Rebuilt through the model rather than",),
    "quackd/host.py": (
        "of a small model on the",
        '"a model"',
        "and the detector's model and device.",
        "as the board's model reported it:",
        "spent in the model, so",
        '"detect.model"',
    ),
    "quackd/onboard/config.py": ("model:",),
    "quackd/onboard/wizard.py": ('"its model (e.g. g1-edu)"',),
    "quackd/preflight.py": ("simulator is the model settling under",),
    "quackd/registry.py": (
        "a key this model no longer",
        "one fetches a model from upstream,",
    ),
    "quackd/sim2d/clock.py": ("the arm the model's timestep",),
    "quackd/verbs/core.py": ("second, a model of the",),
    "tests/conftest.py": ("physics backend's downloaded model lives in",),
    "tests/jetson_fixtures.py": ('"proc/device-tree/model"',),
    "tests/test_cli_places.py": ("and the verb's model is what",),
    "tests/test_controller_vla.py": ("simulator's stand-in model, against",),
    "tests/test_deprecated_backends.py": ("than load a model, is",),
    "tests/test_executor.py": ("# honest model of the",),
    "tests/test_find.py": ("here without the model",),
    "tests/test_loop.py": ("by standing the model up; upstream",),
    "tests/test_mqtt_bus.py": ("# same models, same",),
    "tests/test_pilots.py": ('"""Rebuilt through the model, so',),
    "tests/test_pricing.py": ("than a pricing model: it",),
    "tests/test_robot_registry.py": ('a spec, `{"model":',),
    "web/README.md": (
        "model and the",
        "from the compiled model's own",
    ),
    "web/index.html": (
        "The robot model and its",
        '<a id="model-link" href="https://github.com/pollen-robotics/microduck_rl">Pollen',
    ),
    "web/src/app.js": ('$("model-link").href = UPSTREAM.model;',),
    "web/src/providers.js": (
        "model: llmId,",
        'contents.push({ role: "model", parts:',
    ),
}
"""The rest, file by file, as the sentence says it. Each is a simulator's, a policy's, a detector's,
a board's or a vendor's, and the sort that wrote this list read each one."""

_WHOLE = [re.compile(pattern) for pattern in WHOLE_FILES]
SPAN = re.compile(r"`[^`\n]*`")


def files_read() -> list[str]:
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=REPO,
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8")
    return sorted(
        path
        for path in set(listed.split("\0"))
        if path
        and READ.fullmatch(path)
        and not any(pattern.fullmatch(path) for pattern in _WHOLE)
        and (REPO / path).exists()
    )


def read(path: str) -> str | None:
    try:
        with open(REPO / path, encoding="utf-8", newline="") as f:
            return f.read().replace("\r\n", "\n")
    except UnicodeDecodeError:
        return None


def _markdown(text: str) -> Iterator[tuple[int, str]]:
    fence = False
    for number, line in enumerate(text.split("\n"), 1):
        if line.lstrip().startswith("```"):
            fence = not fence
            continue
        if not fence:
            line = re.sub(r"\]\([^)]*\)", "]", SPAN.sub("", line))
            yield number, re.sub(r"https?://\S+", "", line)


def _python(text: str) -> Iterator[tuple[int, str]]:
    kinds = (tokenize.COMMENT, tokenize.STRING, getattr(tokenize, "FSTRING_MIDDLE", -1))
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        said = token.string
        if token.type not in kinds or re.fullmatch(
            r"""[rRbBuUfF]*(["'])\s*models?\s*\1""", said, re.IGNORECASE
        ):
            continue
        # Before 3.12 an f-string is one token with its expressions in it, and an expression is
        # code rather than a sentence: from 3.12 the tokenizer has already taken them out.
        if token.type == tokenize.STRING and re.match(r"[rRbBuU]*[fF]", said):
            said = re.sub(r"\{[^{}\n]*\}", "", said)
        for offset, part in enumerate(said.split("\n")):
            yield token.start[0] + offset, SPAN.sub("", part)


def _plain(text: str) -> Iterator[tuple[int, str]]:
    for number, line in enumerate(text.split("\n"), 1):
        yield number, SPAN.sub("", line)


def our_words(path: str, text: str) -> Iterator[tuple[int, str]]:
    """Each line of a file as what it says in its own words, without what it quotes or names."""
    reader = _markdown if path.endswith(".md") else _python if path.endswith(".py") else _plain
    return reader(text)


def without_anywhere(words: str) -> str:
    for pattern in ANYWHERE_RE:
        words = pattern.sub("", words)
    return words


def unexplained(path: str, text: str) -> list[str]:
    """The uses in a file that no list here gives a reason for, as `line: words`."""
    found = []
    pieces = SENTENCES.get(path, ())
    for number, words in our_words(path, text):
        left = without_anywhere(words)
        for piece in pieces:
            left = left.replace(piece, "")
        if NOT_THE_WORD.search(left):
            found.append(f"{path}:{number}: {words.strip()[:120]}")
    return found


def test_the_text_that_means_an_llm_says_llm() -> None:
    """Say LLM, or say why not in one of the lists above. "An LLM" takes "an"."""
    found = []
    for path in files_read():
        text = read(path)
        if text is not None and NOT_THE_WORD.search(text):
            found += unexplained(path, text)
    assert not found, "\n".join(found[:40])


def test_every_sentence_listed_is_still_there() -> None:
    """A list of exemptions nobody prunes ends up excusing whatever somebody writes next."""
    gone = []
    for path, pieces in SENTENCES.items():
        text = read(path) if (REPO / path).exists() else None
        said = "\n".join(without_anywhere(w) for _n, w in our_words(path, text)) if text else ""
        gone += [f"{path}: {piece!r}" for piece in pieces if piece not in said]
    assert not gone, "\n".join(gone)


@pytest.mark.parametrize(
    "words",
    [
        "The model asked.",
        "An AI model asked.",
        "Two models asked.",
        "What the model's asked.",
        "A local model, no key.",
    ],
)
def test_the_word_is_caught_in_a_page_a_comment_and_a_string(words: str) -> None:
    """The guard reads what it says it reads: a sentence fails on each kind of file."""
    assert unexplained("a-page.md", words)
    assert unexplained("a_module.py", f"# {words}\n")
    assert unexplained("a_module.py", f'"""{words}"""\n')
    assert unexplained("a_module.py", f'SAID = "{words}"\n')


@pytest.mark.parametrize(
    "words",
    [
        "`model` in a span names a field.",
        "[a page](docs/models/README.md) links somewhere.",
        "```\nthe model said so\n```",
        "The Model Context Protocol is a name.",
        "The threat model and the physics model are no LLM.",
    ],
)
def test_what_a_page_quotes_or_names_is_left_alone(words: str) -> None:
    assert not unexplained("a-page.md", words)


# ── names ──────────────────────────────────────────────────────────────────────────────────────

RETIRED_NAMES = {
    "ModelSpec": "LLMSpec",
    "models_for": "llms_for",
    "default_model_for": "default_llm_for",
    "model_id": "llm_id",
    "find_model": "find_llm",
    "resolve_model": "resolve_llm_id",
    "resolve_decision_model": "resolve_decision_llm",
    "MODEL_KINDS": "LLM_KINDS",
    "list_models_cmd": "list_llms_cmd",
}
"""A Python name that meant an LLM, and what it is now."""

NAMES_MAY_STAY: dict[str, str] = {
    r"quackd/(radio|boards|perception)/.*": "the radio's model, a computer's, a detector's",
    r"quackd/(host|mcp_server)\.py": "the detector's, and the manifest's",
    r"quackd/lan/.*|quackd/passport/.*|quackd/adapters/.*": "a robot's identity",
    r"quackd/onboard/(body|config|wizard|preflight)\.py": "a robot's or a computer's",
    r"quackd/duckfile/.*": "the manifest's",
    r"quackd/agent/prompts\.py": "the manifest's",
}
OWNERS_MAY_STAY = {
    "manifest",
    "manifests",
    "robot",
    "facts",
    "found",
    "client",
    "aio",
    "sim",
    "lerobot",
}
"""An attribute of these is a robot's, a computer's, a vendor SDK's or the simulator's module."""

LINES_MAY_STAY = (
    "/proc/device-tree/model",
    "could not read {model}",
    "predict(state, questions, model=",
    "model=self.llm_id,",
)
"""A vendor's keyword, and the one local name that holds a path to a computer's model."""


def _names(path: str) -> Iterator[tuple[int, str, str, str]]:
    text = read(path)
    if text is None:
        return
    source = text.split("\n")
    tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    for i, token in enumerate(tokens):
        if token.type == tokenize.NAME and not keyword.iskeyword(token.string):
            owner = tokens[i - 2].string if i >= 2 and tokens[i - 1].string == "." else ""
            yield token.start[0], token.string, owner, source[token.start[0] - 1]


def test_no_name_in_quackd_says_the_word_where_it_means_an_llm() -> None:
    allowed = [re.compile(pattern) for pattern in NAMES_MAY_STAY]
    found = []
    code = subprocess.run(
        ["git", "ls-files", "quackd/*.py", "scripts/*.py"],
        cwd=REPO,
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8")
    for path in code.split():
        if not (REPO / path).exists():
            continue
        spared = any(pattern.fullmatch(path) for pattern in allowed)
        for number, name, owner, line in _names(path):
            if name in RETIRED_NAMES:
                found.append(f"{path}:{number}: {name} is {RETIRED_NAMES[name]} now")
            elif (
                name in ("model", "models")
                and not spared
                and owner not in OWNERS_MAY_STAY
                and not any(piece in line for piece in LINES_MAY_STAY)
            ):
                found.append(f"{path}:{number}: {line.strip()[:100]}")
    assert not found, "\n".join(found)


# ── what the rename promised ───────────────────────────────────────────────────────────────────


def test_the_old_command_name_is_refused_and_the_new_one_lists() -> None:
    """Renamed with no alias, at the owner's word, so a script that still types `list-models` stops
    rather than carrying on under a spelling nobody maintains, as one that typed `quackd trace`
    does. The phrase is asserted and not the styled name, as `tests/test_cli_log.py` says why."""
    from typer.testing import CliRunner

    from quackd.cli import app
    from tests.conftest import help_text, plain_text

    runner = CliRunner()
    assert runner.invoke(app, ["list-llms"]).exit_code == 0
    old = runner.invoke(app, ["list-models"], env={"COLUMNS": "200"})
    assert old.exit_code == 2, old.output
    assert "No such command" in plain_text(old.output), old.output
    listed = help_text(["--help"])
    assert "list-llms" in listed and "list-models" not in listed, listed


@pytest.mark.parametrize(
    ("kind", "data", "says"),
    [
        (
            "run_start",
            {"duck": "x", "provider": "openai", "transport": "mock"},
            "provider=openai LLM=gpt-6-sol",
        ),
        (
            "llm_request",
            {"step": 0, "messages": 1, "images": 0, "provider": "openai"},
            "to openai gpt-6-sol",
        ),
    ],
)
def test_a_run_recorded_before_the_rename_replays_as_it_did(
    kind: str, data: dict[str, object], says: str
) -> None:
    """A record says what its version wrote, so `quackd log` reads `model` as well as `llm_id`."""
    from quackd.log import LogEvent, render_lines

    old = render_lines(LogEvent(kind, 0.0, {**data, "model": "gpt-6-sol"}))
    new = render_lines(LogEvent(kind, 0.0, {**data, "llm_id": "gpt-6-sol"}))
    assert old == new
    assert says in old[0][0], old


def test_a_shadow_row_recorded_before_the_rename_names_the_pilots_verb() -> None:
    from quackd.log import LogEvent, render_lines

    row = {
        "choice": "stop",
        "probability": 0.9,
        "agrees": True,
        "decision_latency_s": 0.3,
        "llm_latency_s": 2.1,
    }
    old = render_lines(LogEvent("decision_shadow", 0.0, {**row, "model_verb": "stop"}))
    new = render_lines(LogEvent("decision_shadow", 0.0, {**row, "llm_verb": "stop"}))
    assert old == new
    assert "vs pilot LLM stop" in old[0][0], old


def test_the_example_logs_read_either_key() -> None:
    """E114's record says `model`, and a run from here on says `llm_id`. A log keeps what its run
    wrote, so the script that writes one has to read both."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "example_log", REPO / "scripts" / "example_log.py"
    )
    assert spec is not None and spec.loader is not None
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    assert script._pilot({"provider": "openai", "model": "gpt-6-sol"}) == "openai:gpt-6-sol"
    assert script._pilot({"provider": "openai", "llm_id": "gpt-6-sol"}) == "openai:gpt-6-sol"
    assert script._pilot({"provider": "fake"}) == "fake"
