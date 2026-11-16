"""Text that means an LLM says LLM, and text about every kind of model says models: the owner's
rule, for everything here that somebody reads.

`tests/test_example_logs.py` holds the run logs to it, and this holds the rest of the repository.
What a page quotes is left out the way that test leaves it out: a fenced block, a code span and a
link target are real output and names. Python is read through its tokenizer, so a name is no
sentence.

The word still appears where it means something else: a policy's, a detector's, a board's, a
pydantic class, a proper name. Each of those is listed below with its reason, so a new
use fails here until somebody says why it may stay. Adding a sentence to `SENTENCES` is that saying.

It also appears where it means every kind of model at once: the pilot LLM, the decision LLM and
the learned policy, or the pages, the command and the rows that list all three. A word for one
kind there would leave the other two out, so those say models, and `MODELS_IN_GENERAL` lists
each (the owner's word, 2026-11-14).
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
    # history
    r"docs/adr/0030-.*|docs/adr/0047-.*": "ADRs about the simulators, kept as written",
    # policies
    r"adapters/lerobot/src/roboglot_lerobot/policy/.*": "learned policies",
    r"roboglot/adapters/policies\.py": "learned policies",
    r"docs/guides/policies\.md": "learned policies",
    r"docs/adr/006[01]-.*": "ADRs about learned policies",
    # detectors, the radio and boards
    r"bridge/jetson/.*": "the daemon's detector",
    r"roboglot/perception/.*": "a detector's model",
    r"roboglot/radio/.*": "the radio's log-distance model",
    r"roboglot/boards/.*": "a computer's model",
    # somebody else's words
    r"NOTICE": "licences",
    r"docs/reference/licenses\.md": "licences",
    r"docs/passport/prior-art\.md": "what other standards call a robot's model",
    # tests that have to spell it, which are the only tests that may
    r"tests/test_upstream_api\.py": "a lens's distortion model",
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
    r"log-distance model|textbook model": "the radio",
    r"actuator model": "a servo",
    r"model numbers?": "a servo",
    r"yolo model": "a detector",
    r"vision-language-action model": "a learned policy",
    r"base model": "a learned policy",
    r"/proc/device-tree/model": "a computer's name for itself",
    r"vendor, model\b|maker, model\b|maker and model\b": "a robot's identity",
    r"list-models": "the command that lists every kind of model",
    r"\bmodels/[\w./#-]*": "the folder that holds every kind of model",
}
ANYWHERE_RE = [re.compile(pattern, re.IGNORECASE) for pattern in ANYWHERE]

SENTENCES: dict[str, tuple[str, ...]] = {
    "PLAN.md": ("refuses in every model it loads,",),
    "README.md": (
        "per frame, no model download. Bearing",
        "names and any model it names",
        ", with its model, and",
        "robot and which model it is.",
        "it is a model followed rather",
    ),
    "RELEASING.md": ("schema to its model;",),
    "SECURITY.md": (
        "model). It",
        "and refuses a model the checkpoint",
        "code trusted, a model a checkpoint",
    ),
    "adapters/alohamini/src/roboglot_alohamini/upstream_api.py": (
        "Roboglot derives the model from the",
    ),
    "adapters/alohamini/src/roboglot_alohamini/verbs.py": ("the two 6dof models have",),
    "adapters/lerobot/src/roboglot_lerobot/real.py": (
        "answering as another model",
        "(expected model: <M>)up.HANDSHAKE_NAMES_THE_ID`),",
    ),
    "adapters/lerobot/src/roboglot_lerobot/upstream_api.py": (
        '"src/lerobot/model/kinematics.py"',
        "reading on a model of the",
        'name -> Motor(id, model, norm_mode)"',
        "- <N> (expected model: <M>)'",
        "a servo of the SO-101's model answers",
    ),
    "adapters/microduck/src/roboglot_microduck/upstream_api.py": (
        '"(DEFAULT_POSITION, duck-control/src/model.rs:37-53), so',
        "word on its model, and",
    ),
    "adapters/rosbridge/src/roboglot_rosbridge/upstream_api.py": ("had one) or model a tool",),
    "adapters/toddlerbot/src/roboglot_toddlerbot/verbs.py": ("returns a model of the same shape",),
    "docs/adapters/alohamini/README.md": ("and motors per model |",),
    "docs/adapters/lerobot/README.md": (
        "model a checkpoint",
        "revision, once per model. That",
        "revision. A pinned model a processor",
        "pinned model whose directory",
        "adapter onto the model, or,",
        "and the pinned models, and",
        "into the model and none",
        "answered as another model. LeRobot's",
        "reading on a model of the",
        "answered as another model: one",
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
    ),
    "docs/adapters/lerobot/first-run.md": ("fact about the model. The",),
    "docs/adapters/lerobot/hardware-checklist.md": (
        "fact about the model. If",
        "than about the model.",
    ),
    "docs/adapters/microduck/README.md": ("update to the model cannot make",),
    "docs/adapters/writing-an-adapter.md": (
        "because the manifest model and the",
        "enforced by the model itself",
    ),
    "docs/adr/0001-language.md": ("params, transport models),  +",),
    "docs/adr/0005-glot-spec-v0.md": ("model () and",),
    "docs/adr/0008-perception-features-not-frames.md": ("whose model runs on",),
    "docs/adr/0016-team-lockstep-clock.md": ("the honest hardware-shaped model and may",),
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
    (
        "docs/adr/0074-a-computer-roboglot-cannot-recognise-is-named-and-says-what-it-"
        "calls-itself.md"
    ): ("that names no model. The",),
    "docs/concepts/architecture.md": (
        "through: the manifest's model, body,",
        ", its detector's model and device",
        "spent in the model ( and",
    ),
    "docs/design/memory.md": ("means an embedding model in the",),
    "docs/design/robot-passport-phase-7.md": ("own Agent Card model parsing a",),
    "docs/faq.md": ("switch on the model training toggle",),
    "docs/guides/decision-llms/README.md": (
        "charged | [their models page] ([jev])",
        "([models], re-read",
        "([models]), except that",
    ),
    "docs/guides/decision-llms/jev.md": ("1.13 ([their models page], read",),
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
    "docs/guides/openrouter.md": (
        "the model training toggle",
        "accepted on any model ID at",
        'the newest concrete model", and',
    ),
    "docs/models/pilot-llms.md": ("Gemini Robotics model that does",),
    "docs/models/vendors.md": ("the NVIDIA Open Model",),
    "docs/models/vlas.md": (
        "robotics model at all,",
        "refuses in every model it loads",
    ),
    "docs/passport/compatibility.md": ("model, a",),
    "docs/passport/naming.md": ("RCP, after the Model Context",),
    "docs/passport/why.md": ("and is a model to follow",),
    "docs/reference/manifest-spec.md": (
        "The model is core",
        "## Invariants the model enforces",
    ),
    "roboglot/adapters/export.py": ("regenerates  from the model.",),
    "roboglot/adapters/passport.py": ("than to its model, comes",),
    "roboglot/agent/decision/catalogue.py": (
        "([their models page](https://docs.typesafe.ai/models), read",
    ),
    "roboglot/agent/loop.py": (
        "and its detector's model and device.",
        "bytes`, a model's out-of-memory",
    ),
    "roboglot/agent/providers/anthropic.py": ('supported on this model"',),
    "roboglot/agent/providers/catalogue.py": (
        "embedding and OCR models are not",
        "supported for this model.False` is asked",
        "to the 2.5 models to users",
        '"Our previous-generation Flash model"',
        '"our legacy Flash model" to',
        "discards the model's",
        'supported with this model", and',
        'pricing for our legacy models?"',
        'under "Recommended models"',
        '"Older models" ("These models are no longer the first',
        "overview's own Latest Models are",
        '"the latest flagship model series"',
    ),
    "roboglot/agent/providers/factory.py": (
        "LLM from /v1/models when none",
        "#: OpenRouter's model-variants page",
    ),
    "roboglot/agent/providers/fake.py": ("come from whatever model is loaded,",),
    "roboglot/agent/providers/openai.py": ("this model. Supported",),
    "roboglot/agent/providers/openrouter.py": (
        "builds its own models for the",
        '"/models"',
        '"openrouter-models"',
        "build its own models for keeps",
    ),
    "roboglot/agent/providers/pricing.py": ("rather than the model, because",),
    "roboglot/agent/providers/qwen.py": ("models do not support setting",),
    "roboglot/cli.py": (
        'entry of /v1/models."',
        '"REPO@REVISION of a model the checkpoint',
        "moves. Once per model. A",
    ),
    "roboglot/doctor.py": (
        "its model, and",
        "/models",
        '"answered /models with something',
        "(GET /v1/models, 1.5",
        "servers (GET /v1/models, 1.5",
    ),
    "roboglot/glotfile/export.py": ("drifts from the model.",),
    "roboglot/glotfile/schema.py": (
        "executor trusts this model and nothing",
        "cap is a model validator",
    ),
    "roboglot/team/pilots.py": ("Rebuilt through the model rather than",),
    "roboglot/host.py": (
        "of a small model on the",
        '"a model"',
        "and the detector's model and device.",
        "as the board's model reported it:",
        "spent in the model, so",
        '"detect.model"',
    ),
    "roboglot/onboard/config.py": ("model:",),
    "roboglot/onboard/wizard.py": ('"its model (e.g. g1-edu)"',),
    "roboglot/registry.py": ("a key this model no longer",),
    "roboglot/verbs/core.py": ("second, a model of the",),
    "tests/jetson_fixtures.py": ('"proc/device-tree/model"',),
    "tests/test_cli_places.py": ("and the verb's model is what",),
    "tests/test_described.py": ("model: walker",),
    "tests/test_executor.py": ("# honest model of the",),
    "tests/test_microduck_body.py": ("a word naming some model", '"a-model"'),
    "tests/test_mqtt_bus.py": ("# same models, same",),
    "tests/test_pilots.py": ('"""Rebuilt through the model, so',),
    "tests/test_pricing.py": ("than a pricing model: it",),
    "tests/test_robot_registry.py": ('a spec, `{"model":',),
}
"""The rest, file by file, as the sentence says it. Each is a robot's, a policy's, a detector's, a
board's, a servo's, a pydantic class's or a vendor's, and the sort that wrote this list read each
one."""

MODELS_IN_GENERAL: dict[str, tuple[str, ...]] = {
    "CONTRIBUTING.md": ("Then the models pages, which",),
    "README.md": (
        "Which models work",
        "A run has up to three models in it",
        "in the section on models above",
    ),
    "docs/README.md": (
        "[Models]",
        "## Models",
        "which models Roboglot can fly",
        "and models have",
        "roles a model can have",
        "for which models Roboglot names",
        "How to set a model up",
    ),
    "docs/adr/0062-model-tables-are-rendered-from-the-catalogues.md": (
        "The model tables are rendered",
        "kind of model, so on",
        "robots there, models here",
        "says which models work",
        "everything about models",
    ),
    "docs/models/README.md": (
        "# Models",
        "up to three models in it",
        "the other half: the models",
        "Every table of models",
        "So a model is added",
    ),
    "docs/models/decision-llms.md": ("the optional second model of a run",),
    "docs/models/pilot-llms.md": ("the only model a run needs", "[the models page]"),
    "docs/models/vendors.md": ("whose models Roboglot",),
    "docs/models/vlas.md": ("the third model a run can have",),
    "roboglot/cli.py": ("Which models to list.", "List the models Roboglot can use"),
    "scripts/models_docs.py": ("names a model, a vendor",),
}
"""Where the word means every kind of model at once, file by file as the sentence says it: the
pilot LLM, the decision LLM and the learned policy together, or what lists all three."""

EVERY_KIND = re.compile(
    r"\bLLMs (?:and|or) (?:learned )?polic(?:y|ies)\b|\ban LLM or a (?:learned )?policy\b"
    r"|\btwo LLMs and a policy\b",
    re.IGNORECASE,
)
"""What 0.32.0 wrote where it meant every kind of model, which says models again."""

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
    pieces = SENTENCES.get(path, ()) + MODELS_IN_GENERAL.get(path, ())
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
    for path, pieces in [*SENTENCES.items(), *MODELS_IN_GENERAL.items()]:
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
        "The threat model and the data model are no LLM.",
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
}
"""A Python name that meant an LLM, and what it is now."""

NAMES_FOR_EVERY_KIND = {
    "LLM_KINDS": "MODEL_KINDS",
    "list_llms_cmd": "list_models_cmd",
}
"""A Python name that said LLM where it meant every kind of model, and what it is now."""

NAMES_MAY_STAY: dict[str, str] = {
    r"roboglot/(radio|boards|perception)/.*": "the radio's model, a computer's, a detector's",
    r"roboglot/(host|mcp_server)\.py": "the detector's, and the manifest's",
    r"roboglot/lan/.*|roboglot/passport/.*|roboglot/adapters/.*": "a robot's identity",
    r"roboglot/onboard/(body|config|wizard|preflight)\.py": "a robot's or a computer's",
    r"roboglot/glotfile/.*": "the manifest's",
    r"roboglot/agent/prompts\.py": "the manifest's",
}
OWNERS_MAY_STAY = {
    "manifest",
    "manifests",
    "robot",
    "facts",
    "found",
    "client",
    "aio",
    "lerobot",
}
"""An attribute of these is a robot's, a computer's or a vendor SDK's."""

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


def test_no_name_in_roboglot_says_the_word_where_it_means_an_llm() -> None:
    allowed = [re.compile(pattern) for pattern in NAMES_MAY_STAY]
    found = []
    code = subprocess.run(
        ["git", "ls-files", "roboglot/*.py", "scripts/*.py"],
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
            elif name in NAMES_FOR_EVERY_KIND:
                found.append(f"{path}:{number}: {name} is {NAMES_FOR_EVERY_KIND[name]} now")
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
    """`roboglot list-models` lists every kind of model, so it has the name it had before 0.32.0
    again, and `list-llms` has no alias, at the owner's word, as `list-models` had none then. A
    script that still types it stops rather than carrying on under a spelling nobody maintains,
    as one that typed `roboglot trace` does, and Click's refusal names the command it meant. The
    phrase is asserted and not the styled name, as `tests/test_cli_log.py` says why."""
    from typer.testing import CliRunner

    from roboglot.cli import app
    from tests.conftest import help_text, plain_text

    runner = CliRunner()
    assert runner.invoke(app, ["list-models"]).exit_code == 0
    old = runner.invoke(app, ["list-llms"], env={"COLUMNS": "200"})
    assert old.exit_code == 2, old.output
    refused = plain_text(old.output)
    assert "No such command" in refused and "list-models" in refused, old.output
    listed = help_text(["--help"])
    assert "list-models" in listed and "list-llms" not in listed, listed


def test_text_about_every_kind_of_model_says_models() -> None:
    """0.32.0 wrote `LLMs and policies` where the word meant every kind of model a run can have,
    and named a folder and a command for LLMs that list policies too. They say models again, and
    a sentence that counts the kinds as LLMs and a policy fails here. A released section of the
    changelog and a run's log keep what they said."""
    kept = re.compile(r"CHANGELOG\.md|docs/examples/lerobot/e\d+/.*|tests/test_llm_wording\.py")
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=REPO,
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8")
    found = []
    for path in sorted(set(listed.split("\0"))):
        if not path or not READ.fullmatch(path) or kept.fullmatch(path):
            continue
        text = read(path) if (REPO / path).exists() else None
        if text is None or not EVERY_KIND.search(text):
            continue
        found += [
            f"{path}:{number}: {words.strip()[:120]}"
            for number, words in our_words(path, text)
            if EVERY_KIND.search(words)
        ]
    assert not found, "\n".join(found)
    assert not (REPO / "docs" / "llms").exists(), "the model pages are docs/models/"


@pytest.mark.parametrize(
    "words", ["Which LLMs and policies work", "A run has up to two LLMs and a policy in it."]
)
def test_the_kinds_counted_as_llms_are_caught(words: str) -> None:
    assert EVERY_KIND.search(words)


@pytest.mark.parametrize(
    ("kind", "data", "says"),
    [
        (
            "run_start",
            {"glot": "x", "provider": "openai", "transport": "mock"},
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
    """A record says what its version wrote, so `roboglot log` reads `model` as well as `llm_id`."""
    from roboglot.log import LogEvent, render_lines

    old = render_lines(LogEvent(kind, 0.0, {**data, "model": "gpt-6-sol"}))
    new = render_lines(LogEvent(kind, 0.0, {**data, "llm_id": "gpt-6-sol"}))
    assert old == new
    assert says in old[0][0], old


def test_a_shadow_row_recorded_before_the_rename_names_the_pilots_verb() -> None:
    from roboglot.log import LogEvent, render_lines

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
