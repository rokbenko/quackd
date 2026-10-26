"""The LeRobot pipeline behind `quackd policy serve --policy REPO@REVISION`.

It runs in the policy server's process and in no other. A checkpoint's processors are code
(`upstream_api.PROCESSOR_CLASS_IMPORT`), so nothing on the arm's side imports this module, and
it imports torch, LeRobot and huggingface_hub only inside `load` and the runner that builds: the
server imports it with none of them installed, and serves a scripted policy that way.

**Loading a checkpoint reads before it builds, and refuses rather than guesses** (`load`):

1. `config.json` and both processor JSONs are fetched at the revision named, and nothing else
   of the repository yet (`upstream_api.CHECKPOINT_FILES`).
2. They are checked. The policy is one of the types whose processors were read at the pin
   (`upstream_api.POLICY_TYPES`), and its features are a state, images and an action. Every
   processor step is named by a registry name read at the pin for that type (`STEPS_BY_TYPE`),
   and a step named by a `class` key is refused, so nothing is imported from a path a
   checkpoint chose. A step that could trust a repository's own code is told not to, and a
   model a checkpoint names inside itself, SmolVLA's backbone or a tokenizer, is refused unless
   `--pin REPO@REVISION` says which revision to fetch it at
   (`upstream_api.TOKENIZER_TRUSTS_REMOTE_CODE`). A SmolVLA config that names no backbone is
   refused too, since LeRobot would fill in its own. A pin is a commit or a tag, and never a
   branch, which moves (`check_fixed`). What a type's own code reads besides is read as it
   reads it (`check_family`).
3. The rate is `--fps`, or else the fps of the dataset `train_config.json` names, read at the
   revision it names, a commit or a tag, or else the server refuses to start
   (`upstream_api.TRAIN_DATASET`).
4. Only then are the weights, the processors' state files and the pinned models fetched, the
   pinned models with no code and no pickle in what is fetched, and a tokenizer with no weights
   either (`nested_patterns`), and refused if their directory holds any all the same, maps a
   class to code or holds a PEFT adapter (`check_pinned`). The policy is built on the device
   `auto_select_torch_device` picks (`upstream_api.AUTO_DEVICE`), with every weight the file
   holds loaded into it or none (`build_policy`). An ACT's ImageNet backbone is never fetched
   (`upstream_api.ACT_BACKBONE_WEIGHTS`), and both processors' device steps are put where the
   policy runs and where its answers go (`upstream_api.DEVICE_OVERRIDE`).

Every repository fetched is recorded with its revision (`Checkpoint.loaded`), and the server
says them in `/v1/policy`.

**A step follows upstream's own loop** (`upstream_api.POLICY_PIPELINE`, `LeRobotRunner`): the
arm's reading goes through `build_inference_frame`, named by the session's motors in its bus's
order, then the pre-processor, then `predict_action_chunk` cut to `n_action_steps`, then the
post-processor over the whole chunk at once, and `make_robot_action` makes each row a goal per
motor. One lock holds the three together, so a reset never lands between them, and a reset
resets the policy and both processors. A policy asked every tick (an ACT with temporal
ensembling) goes through `select_action` instead, one action a step. A pi05 or a pi0 that
learned relative actions stays on chunks: its whole chunk is made absolute against the state it
was predicted from, which is how its training made it relative, and not re-anchored each tick
as LeRobot's own loop does (`upstream_api.RELATIVE_ACTIONS`, `VLA_PIPELINE`).
"""

from __future__ import annotations

import contextlib
import fnmatch
import functools
import importlib.util
import json
import math
import re
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from quackd_lerobot.policy import protocol as wire
from quackd_lerobot.policy import upstream_api as up
from quackd_lerobot.policy.runner import Chunk, Features, Observation

REPO_AT_REVISION = re.compile(r"^([A-Za-z0-9][\w.\-]*/[\w.\-]+)@([\w.\-/]+)$", re.ASCII)
"""`owner/name@revision`: a Hub repository at a revision, the way `--policy` names a checkpoint
and `--pin` a model a checkpoint names inside itself."""
REPO_ID = re.compile(r"^[A-Za-z0-9][\w.\-]*/[\w.\-]+$", re.ASCII)
MAX_SPEC_CHARS = 150
"""The longest `REPO@REVISION` taken: a Hub id is at most 96 characters, and a revision a
commit's 40 or a short tag, with room left for what `/v1/policy` says it is."""
STEPS_BY_TYPE: Mapping[str, frozenset[str]] = {
    "act": frozenset(up.DEFAULT_STEP_NAMES),
    "smolvla": frozenset(up.DEFAULT_STEP_NAMES + up.SMOLVLA_STEP_NAMES),
    "pi05": frozenset(up.DEFAULT_STEP_NAMES + up.PI05_STEP_NAMES),
    "pi0": frozenset(up.DEFAULT_STEP_NAMES + up.PI0_STEP_NAMES),
    "xvla": frozenset(up.DEFAULT_STEP_NAMES + up.XVLA_STEP_NAMES),
    "groot": frozenset(up.DEFAULT_STEP_NAMES + up.GROOT_STEP_NAMES),
}
"""The processor steps a checkpoint may name, by its type, each by the registry name LeRobot
registers it under: LeRobot's default steps, which are all of ACT's, and those of the type's
own family. GR00T's processors leave the default normaliser pair out and normalise in steps
of their own, and it is allowed the pair as every type is. Each is one of LeRobot's own
classes, found in its registry, never imported by a path, and a type may name no other
type's: an ACT that names a VLA's step is code nobody read for an ACT."""
REFUSED_STEPS: Mapping[str, str] = {
    "action_tokenizer_processor": (
        "which loads its tokenizer trusting the repository's own code unless told not to, and "
        "turns actions into tokens for training, which a server never does: serve a checkpoint "
        "whose processors tokenise no actions (upstream_api.TOKENIZER_TRUSTS_REMOTE_CODE)"
    ),
    "xvla_rotation_6d_to_axis_angle": (
        "which turns an end-effector rotation into axis-angle and the gripper into -1 or 1, and an "
        "SO-101 takes joint positions: serve an X-VLA fine-tuned in 'auto' mode, whose "
        "post-processor has no such step (upstream_api.XVLA_LIBERO_ROTATION)"
    ),
    **{
        name: (
            "which only GR00T N1.5's processors name, and LeRobot 0.6.1 serves GR00T N1.7 alone: "
            "serve a GR00T fine-tuned on N1.7 (upstream_api.GROOT_N1_5_REMOVED)"
        )
        for name in up.GROOT_N1_5_STEP_NAMES
    },
}
"""Steps no type may name, each with why in the words its refusal goes on with, refused before
the sentence that lists what a type allows, since that one would not say what is wrong."""
REFUSED_TYPES: Mapping[str, str] = {
    "pi0_fast": (
        "which turns its answer into actions with the FAST tokenizer's own Python code from the "
        "Hub, loaded with trust_remote_code=True inside the policy, and quackd runs no code a "
        "repository brings with it: serve a pi0 or pi05 instead (upstream_api.PI0_FAST_REMOTE_CODE)"
    ),
}
"""Types quackd has read and will not serve, each with why in the words its refusal goes on
with, refused before anything else of a checkpoint is read."""
NESTED_STEP_KEYS = ("tokenizer_name", "model_name")
"""The keys of a step's config that name a model to load from the Hub, a tokenizer each, or
GR00T's backbone's tokenizer and processors (`upstream_api.TOKENIZER_TRUSTS_REMOTE_CODE`,
`GROOT_VLM_PROCESSOR`), so `TOKENIZER_FILES` are what is fetched of it."""
NESTED_CONFIG_KEYS = {"smolvla": ("vlm_model_name",), "groot": ("base_model_path",)}
"""The keys of `config.json` that name one, by the type that has them: SmolVLA's backbone and
GR00T's base, which the policy is built from, so `NESTED_FILES` are what is fetched of it. A
config that leaves one out is refused, because LeRobot fills it with its own default, a Hub
name it would then load at no revision (`upstream_api.TOKENIZER_TRUSTS_REMOTE_CODE`,
`GROOT_BASE_MODEL`)."""
TRUST_KEY = "trust_remote_code"
TOKENIZER_FILES = ("*.json", "*.txt", "*.model", "*.jinja")
"""What is fetched of a pinned model a step names: its configs and tokenizer files, which are
all a tokenizer loads, and never its weights, a `.py` that remote code would run from, nor a
pickle a load would run."""
NESTED_FILES = (*TOKENIZER_FILES, "*.safetensors")
"""What is fetched of a pinned model `config.json` names, one the policy is built from: the
same, and its safetensors weights."""
NESTED_DOCS = ("*.md", ".gitattributes")
"""What else a pinned model's directory may hold: a repository's own words about itself, which
nothing loads."""
CODE_MAP_KEY = "auto_map"
"""The key of a transformers config that maps a class to code of the repository's own, or of
another repository's (`upstream_api.TOKENIZER_TRUSTS_REMOTE_CODE`)."""
ADAPTER_CONFIG = "adapter_config.json"
"""The file that makes a directory a PEFT adapter. With peft installed, which the server's
extra does not bring and LeRobot's own `[peft]` and `[groot]` do, transformers building a model
from a directory that holds one loads the
adapter onto it, or, with no `config.json` beside it, loads the base model the adapter names,
which no pin fixes."""
LENIENT_LOADERS = frozenset({"pi05", "pi0"})
"""The policy types whose own `from_pretrained` returns the model without its weights when they
do not load, having printed why (`upstream_api.PI05_FROM_PRETRAINED`, `PI0_FROM_PRETRAINED`)."""
MODEL_PREFIX = "model."
"""What pi05's and pi0's loaders put in front of each weight's name that lacks it."""
COMMIT = re.compile(r"^[0-9a-f]{40}$")
"""A commit, as the Hub's cache names the directory a revision's files land in."""
STATE_FILE = re.compile(r"^[\w.\-]+\.safetensors$", re.ASCII)
"""A processor step's `state_file`: a plain file name beside the JSON, never a path."""
PADDED_TYPES = frozenset({"smolvla", "pi05", "pi0", "xvla"})
"""The policy types that run with some of their images missing
(`upstream_api.MISSING_IMAGES_PADDED`)."""


class PipelineRefused(ValueError):
    """A checkpoint the server will not load, in one sentence that says what to do instead."""


# ── reading what a checkpoint says it is ────────────────────────────────────────────────


def parse_repo(spec: str, flag: str) -> tuple[str, str]:
    """`REPO@REVISION` as (repo, revision), or a refusal that names `flag`."""
    text = spec.strip()
    match = REPO_AT_REVISION.match(text)
    if match is None or len(text) > MAX_SPEC_CHARS:
        raise PipelineRefused(
            f"{flag} takes REPO@REVISION, a Hub repository at a commit or a tag in ASCII, "
            f"such as lerobot/smolvla_base@REVISION, and not {text[:60]!r}"
        )
    return match.group(1), match.group(2)


def parse_pins(pins: Sequence[str]) -> dict[str, str]:
    """Each `--pin REPO@REVISION` as repo -> revision. A repository pinned twice at two
    revisions is refused rather than one of them picked."""
    pinned: dict[str, str] = {}
    for pin in pins:
        repo, revision = parse_repo(pin, "--pin")
        if pinned.get(repo, revision) != revision:
            raise PipelineRefused(f"--pin names {repo} at {pinned[repo]} and at {revision}")
        pinned[repo] = revision
    return pinned


@dataclass(frozen=True)
class Shape:
    """What a checkpoint's `config.json` says it is, checked: its type, the size of its state
    and its action, its images as (key, height, width), its chunking, whether it is asked every
    tick, its action's names where it has them, and whether it pads an image it is not given."""

    policy_type: str
    state: int
    action: int
    images: tuple[tuple[str, int, int], ...]
    chunk_size: int
    n_action_steps: int
    per_tick: bool = False
    action_names: tuple[str, ...] | None = None
    pads_images: bool = False

    @property
    def image_keys(self) -> tuple[str, ...]:
        return tuple(key for key, _, _ in self.images)


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _feature(features: Any, key: str) -> tuple[str, tuple[int, ...]] | None:
    """A feature of `config.json` as (type, shape), or None when it is not one."""
    ft = features.get(key) if isinstance(features, Mapping) else None
    if not isinstance(ft, Mapping) or not isinstance(ft.get("type"), str):
        return None
    shape = ft.get("shape")
    if not isinstance(shape, list | tuple) or not all(_count(n) for n in shape):
        return None
    return ft["type"], tuple(int(n) for n in shape)


def check_config(config: Any, spec: str, *, gpu: bool) -> Shape:
    """A checkpoint's `config.json`, checked, or a refusal that says what is wrong with it.

    The policy is a type the server has read the processors of (`upstream_api.POLICY_TYPES`),
    and a type it read and will not serve is refused first, saying why (`REFUSED_TYPES`); its
    inputs are one state and some images, and its output one action
    (`upstream_api.FEATURE_TYPES`, `FEATURE_KEYS`); its chunking fits the protocol; and a policy
    asked every tick is refused without a GPU, since it has to answer inside one
    (`upstream_api.TICK_MODE`)."""
    if not isinstance(config, Mapping):
        raise PipelineRefused(f"{spec}'s {up.CONFIG_FILE} is not a JSON object")
    policy_type = config.get("type")
    if isinstance(policy_type, str) and policy_type in REFUSED_TYPES:
        raise PipelineRefused(f"{spec} is a {policy_type!r} policy, {REFUSED_TYPES[policy_type]}")
    if policy_type not in up.SERVED_TYPES:
        raise PipelineRefused(
            f"{spec} is a {policy_type!r} policy, and quackd serves "
            f"{', '.join(up.SERVED_TYPES)}, the types whose processors it has read"
        )
    inputs, outputs = config.get("input_features"), config.get("output_features")
    inputs = inputs if isinstance(inputs, Mapping) else {}
    outputs = outputs if isinstance(outputs, Mapping) else {}
    state = _feature(inputs, up.STATE_KEY)
    if state is None or state[0] != "STATE" or len(state[1]) != 1:
        raise PipelineRefused(
            f"{spec} takes no {up.STATE_KEY} of one dimension, which is the arm's reading of "
            "its motors: quackd serves a policy that reads the arm it moves"
        )
    images: list[tuple[str, int, int]] = []
    for key in inputs:
        if key == up.STATE_KEY:
            continue
        ft = _feature(inputs, key)
        if ft is None or ft[0] != "VISUAL" or len(ft[1]) != 3:
            raise PipelineRefused(
                f"{spec} takes {key}, which is not an image, and an arm gives only its motors "
                "and its cameras"
            )
        height, width = ft[1][1], ft[1][2]  # (C, H, W), as config.json keeps an image
        if not re.match(wire.NAME_PATTERN, str(key)) or max(height, width) > wire.MAX_SIDE:
            raise PipelineRefused(
                f"{spec} looks at {str(key)[:80]!r} at {width}x{height}, which is not an image "
                "key and a size the protocol carries"
            )
        images.append((str(key), height, width))
    action = _feature(outputs, up.ACTION_KEY)
    if action is None or action[0] != "ACTION" or len(action[1]) != 1 or len(outputs) != 1:
        raise PipelineRefused(f"{spec} does not answer one {up.ACTION_KEY} of one dimension")
    if state[1][0] > wire.MAX_MOTORS or action[1][0] > wire.MAX_MOTORS:
        raise PipelineRefused(f"{spec} has more than the {wire.MAX_MOTORS} motors a session names")
    if len(images) > wire.MAX_CAMERAS:
        raise PipelineRefused(f"{spec} looks at more than {wire.MAX_CAMERAS} cameras")
    chunk_size = _count(config.get("chunk_size"))
    steps = _count(config.get("n_action_steps"))
    if chunk_size is None or steps is None or steps > chunk_size or steps > wire.MAX_CHUNK:
        raise PipelineRefused(
            f"{spec} says chunk_size {config.get('chunk_size')!r} and n_action_steps "
            f"{config.get('n_action_steps')!r}, and the server plays at most {wire.MAX_CHUNK} "
            "of a chunk it predicted (upstream_api.CHUNK_SIZE)"
        )
    per_tick = policy_type == "act" and config.get("temporal_ensemble_coeff") is not None
    if per_tick and not gpu:
        raise PipelineRefused(
            f"{spec} ensembles its chunks over time (temporal_ensemble_coeff), which asks it "
            "every tick, and this machine has no GPU to answer inside one: serve it on a GPU, "
            "or serve a checkpoint trained without temporal ensembling"
        )
    names = config.get("action_feature_names")
    action_names: tuple[str, ...] | None = None
    if names is not None:
        if not isinstance(names, list) or not all(
            isinstance(n, str) and re.match(wire.NAME_PATTERN, n) for n in names
        ):
            raise PipelineRefused(f"{spec}'s action_feature_names are not names of motors")
        action_names = tuple(names)
    return Shape(
        policy_type=str(policy_type),
        state=state[1][0],
        action=action[1][0],
        images=tuple(images),
        chunk_size=chunk_size,
        n_action_steps=steps,
        per_tick=per_tick,
        action_names=action_names,
        pads_images=policy_type in PADDED_TYPES,
    )


def check_processor(doc: Any, filename: str, spec: str, policy_type: str) -> list[dict[str, Any]]:
    """A processor JSON's steps, each checked, or a refusal naming the step that is not allowed.

    A step is named by a registry name its type allows (`STEPS_BY_TYPE`) or it is refused, one
    no type may name with a sentence of its own (`REFUSED_STEPS`), and a step with a `class`
    key is refused whatever else it says, because loading one imports that path
    (`upstream_api.PROCESSOR_CLASS_IMPORT`). A state file is a plain `.safetensors` name
    beside the JSON."""
    steps = doc.get("steps") if isinstance(doc, Mapping) else None
    if not isinstance(steps, list):
        raise PipelineRefused(f"{spec}'s {filename} is not a processor: it has no list of steps")
    allowed = STEPS_BY_TYPE[policy_type]
    checked: list[dict[str, Any]] = []
    for n, step in enumerate(steps):
        if not isinstance(step, Mapping):
            raise PipelineRefused(f"step {n} of {spec}'s {filename} is not a JSON object")
        if "class" in step:
            raise PipelineRefused(
                f"step {n} of {spec}'s {filename} is named by its class, "
                f"{str(step['class'])[:80]!r}, which loading it would import from wherever that "
                "says, so it is code the checkpoint chose: quackd loads a step by a registry "
                "name it has read, and nothing else"
            )
        name = step.get("registry_name")
        if isinstance(name, str) and name in REFUSED_STEPS:
            raise PipelineRefused(
                f"step {n} of {spec}'s {filename} is {name!r}, {REFUSED_STEPS[name]}"
            )
        if not isinstance(name, str) or name not in allowed:
            raise PipelineRefused(
                f"step {n} of {spec}'s {filename} is {str(name)[:80]!r}, which is not one of the "
                f"processor steps quackd has read and allows for a {policy_type!r} policy: "
                f"{', '.join(sorted(allowed))}"
            )
        config = step.get("config", {})
        if not isinstance(config, Mapping):
            raise PipelineRefused(f"step {n} of {spec}'s {filename} has a config that is not one")
        state = step.get("state_file")
        if state is not None and not (isinstance(state, str) and STATE_FILE.match(state)):
            raise PipelineRefused(
                f"step {n} of {spec}'s {filename} keeps its state in {str(state)[:80]!r}, which "
                "is not a .safetensors file beside it"
            )
        checked.append({"registry_name": name, "config": dict(config), "state_file": state})
    return checked


def nested_models(
    config: Mapping[str, Any], steps: Sequence[Mapping[str, Any]], spec: str
) -> dict[str, str]:
    """Every model a checkpoint names inside itself, as repo -> the key that names it. Each is
    a Hub id, or refused: the server loads nothing by a path a checkpoint wrote. A key of
    `config.json` its type has (`NESTED_CONFIG_KEYS`) is refused when it is missing, since
    LeRobot would load its own default in its place, at no revision. A step's is not: a
    tokenizer step with no name loads nothing and fails to build."""
    found: dict[str, str] = {}
    for key in NESTED_CONFIG_KEYS.get(str(config.get("type")), ()):
        if config.get(key) is None:
            raise PipelineRefused(
                f"{spec}'s {up.CONFIG_FILE} names no {key}, and LeRobot would load its own "
                "default in its place, from the Hub at whatever revision it has that day: serve "
                f"a copy of the checkpoint whose {up.CONFIG_FILE} names it, and pin that with "
                "--pin REPO@REVISION"
            )
    for key, value, _ in _named(config, steps):
        if value is None:
            continue
        if not isinstance(value, str) or not REPO_ID.match(value):
            raise PipelineRefused(
                f"{spec} names {str(value)[:80]!r} as its {key}, which is not a Hub repository "
                "quackd can fetch at a revision you pin"
            )
        found.setdefault(value, key)
    return found


def _named(
    config: Mapping[str, Any], steps: Sequence[Mapping[str, Any]]
) -> list[tuple[str, Any, tuple[str, ...]]]:
    """Every (key, value, what is fetched of it) a checkpoint could name a model by: each key
    `NESTED_CONFIG_KEYS` has for any type, whatever this config's type, and each of
    `NESTED_STEP_KEYS` on every step."""
    named: list[tuple[str, Any, tuple[str, ...]]] = [
        (key, config.get(key), NESTED_FILES) for keys in NESTED_CONFIG_KEYS.values() for key in keys
    ]
    named += [
        (key, s["config"].get(key), TOKENIZER_FILES) for s in steps for key in NESTED_STEP_KEYS
    ]
    return named


def nested_patterns(
    config: Mapping[str, Any], steps: Sequence[Mapping[str, Any]]
) -> dict[str, tuple[str, ...]]:
    """What is fetched of each model a checkpoint names (`nested_models`), by the keys that name
    it. One a step names is a tokenizer, so `TOKENIZER_FILES` and no weight, which a tokenizer
    never loads. One `config.json` names is a model the policy is built from, so `NESTED_FILES`,
    its weights among them, and a repository named both ways, as SmolVLA's backbone is by its
    config and by its tokenizer step, gets those."""
    patterns: dict[str, tuple[str, ...]] = {}
    for _, value, fetched in _named(config, steps):
        if isinstance(value, str) and patterns.get(value) != NESTED_FILES:
            patterns[value] = fetched  # TOKENIZER_FILES is NESTED_FILES without the weights
    return patterns


def pinned_models(nested: Mapping[str, str], pins: Mapping[str, str], spec: str) -> None:
    """Refuse a nested model with no `--pin`, and a `--pin` for a model nothing names."""
    for repo, key in nested.items():
        if repo not in pins:
            raise PipelineRefused(
                f"{spec} names {repo} as its {key}, which would load at whatever revision the "
                f"Hub has that day: pass --pin {repo}@REVISION, a commit or a tag you have read, "
                "and the server fetches it at that revision and at no other"
            )
    for repo in pins:
        if repo not in nested:
            raise PipelineRefused(
                f"--pin names {repo}, and {spec} names no such model: pin only the models it "
                f"names{', which are ' + ', '.join(nested) if nested else ', and it names none'}"
            )


@dataclass(frozen=True)
class Family:
    """What a type's own reading of a checkpoint found, beyond what every type's finds: the 1st
    and 99th percentiles of the state and the action it learned from, for a type that keeps
    them where the normaliser's statistics are not (`LeRobotRunner`), or None."""

    state_quantiles: wire.Quantiles | None = None
    action_quantiles: wire.Quantiles | None = None


PACKAGE_OF = {"tree": "dm-tree"}
"""The package that installs an import name, where the two differ."""


def importable_module(name: str) -> bool:
    """Whether `name` would import here, found without importing it."""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def check_family(
    config: Mapping[str, Any],
    steps: Sequence[Mapping[str, Any]],
    spec: str,
    *,
    importable: Callable[[str], bool] = importable_module,
) -> Family:
    """A checkpoint's config and its processors' steps read the way its own type's code reads
    them, before any pin is checked or any weight fetched, or a refusal that says what in them
    could not drive an arm's joints. ACT, SmolVLA, pi05 and pi0 have nothing past what every type
    is checked for (`check_config`, `check_processor`), so theirs is an empty `Family`. A type
    that answers with packages of its own is refused when they would not import, which
    `importable` says (`upstream_api.GROOT_INFERENCE_PACKAGES`)."""
    if config.get("type") == "xvla":
        _check_xvla(config, steps, spec)
    if config.get("type") == "groot":
        family = _check_groot(config, steps, spec)
        missing = [
            PACKAGE_OF.get(name, name)
            for name in up.GROOT_INFERENCE_MODULES
            if not importable(name)
        ]
        if missing:
            raise PipelineRefused(
                f"{spec} is a GR00T N1.7, which answers with {' and '.join(missing)}, and this "
                f"server cannot import {'them' if len(missing) > 1 else 'it'}: install the "
                'server\'s extra again, uv pip install "quackd[lerobot-vla]", which brings '
                "diffusers and dm-tree (upstream_api.GROOT_INFERENCE_PACKAGES)"
            )
        return family
    return Family()


def _check_xvla(config: Mapping[str, Any], steps: Sequence[Mapping[str, Any]], spec: str) -> None:
    """An X-VLA decodes its answer in the space `action_mode` names, lowered, `ee6d` when it
    names none, and only `auto` is an arm's own joints. Its domain step hands the model a
    domain, which picks a row of every domain-aware layer, so it is a whole number below
    `num_domains` (`upstream_api.XVLA_ACTION_MODES`, `XVLA_PROCESSOR_STEPS`)."""
    mode = config.get("action_mode", up.XVLA_DEFAULT_MODE)
    if not isinstance(mode, str) or mode.lower() != up.XVLA_SERVED_MODE:
        raise PipelineRefused(
            f"{spec} is an X-VLA whose action_mode is {mode!r}, which is not this arm's joints, "
            f"and quackd serves an X-VLA in {up.XVLA_SERVED_MODE!r} mode "
            "(upstream_api.XVLA_ACTION_MODES)"
        )
    domains = _count(config.get("num_domains", up.XVLA_DEFAULT_DOMAINS))
    if domains is None:
        raise PipelineRefused(
            f"{spec}'s num_domains is {config.get('num_domains')!r}, which is not a count of "
            "domains (upstream_api.XVLA_PROCESSOR_STEPS)"
        )
    for step in steps:
        if step["registry_name"] != up.XVLA_DOMAIN_STEP:
            continue
        domain = step["config"].get("domain_id", up.XVLA_DEFAULT_DOMAIN)
        if isinstance(domain, bool) or not isinstance(domain, int) or not 0 <= domain < domains:
            raise PipelineRefused(
                f"{spec}'s {up.XVLA_DOMAIN_STEP} step hands the model domain {domain!r}, and it "
                f"has {domains} domains, numbered from 0: serve a checkpoint whose step names one "
                "of them (upstream_api.XVLA_PROCESSOR_STEPS)"
            )


def _check_groot(
    config: Mapping[str, Any], steps: Sequence[Mapping[str, Any]], spec: str
) -> Family:
    """A GR00T N1.7, read as its processors decode it (`upstream_api.GROOT_DECODE`): no N1.5,
    no LIBERO gripper, a pack step that looks for the cameras its config names, and a decode
    that answers every action group as joints, unnormalised by statistics it has. A checkpoint
    that decodes by grouped statistics keeps its frame in its pack step's `raw_stats`, where the
    normaliser's reading would never find it, so those percentiles are what comes back
    (`upstream_api.GROOT_PERCENTILE_STATS`)."""
    base = config.get("base_model_path")
    n1_5 = isinstance(base, str) and any(mark in base.lower() for mark in up.GROOT_N1_5_BASES)
    if n1_5 or config.get("tokenizer_assets_repo") is not None:
        raise PipelineRefused(
            f"{spec} is a GR00T N1.5 checkpoint, and LeRobot 0.6.1 serves GR00T N1.7 alone: serve "
            "a GR00T fine-tuned on N1.7 (upstream_api.GROOT_N1_5_REMOVED)"
        )
    transform = config.get("action_decode_transform", up.GROOT_AUTO_TRANSFORM)
    named = transform.lower() if isinstance(transform, str) else transform
    if named not in (None, "none", "", up.GROOT_AUTO_TRANSFORM, up.GROOT_LIBERO):
        raise PipelineRefused(
            f"{spec}'s action_decode_transform is {str(transform)[:80]!r}, which is none LeRobot "
            "0.6.1 knows (upstream_api.GROOT_DECODE)"
        )
    if named == up.GROOT_LIBERO or (
        named == up.GROOT_AUTO_TRANSFORM
        and config.get("embodiment_tag") == up.GROOT_LIBERO_EMBODIMENT
    ):
        raise PipelineRefused(
            f"{spec} decodes its gripper with LIBERO's transform, to -1 or 1, and an SO-101's "
            "gripper takes 0..100: serve a GR00T fine-tuned on this arm's own data "
            "(upstream_api.GROOT_DECODE)"
        )
    pack: Mapping[str, Any] = next(
        (s["config"] for s in steps if s["registry_name"] == up.GROOT_PACK_STEP), {}
    )
    cameras = pack.get("video_modality_keys")
    images = [k for k in config.get("input_features", {}) if k.startswith(up.IMAGES_PREFIX)]
    if isinstance(cameras, list):
        unknown = [str(k) for k in cameras if f"{up.IMAGES_PREFIX}{k}" not in images]
        if unknown:
            raise PipelineRefused(
                f"{spec}'s pack step looks for the cameras {', '.join(unknown)[:120]}, which its "
                f"{up.CONFIG_FILE} names no image for, so the model would see other views than it "
                "learned on: serve a checkpoint whose config names every camera its pack step "
                "does (upstream_api.GROOT_IMAGE_FALLBACK)"
            )
    decoders = [
        s
        for s in steps
        if s["registry_name"] in (up.GROOT_DECODE_STEP, up.GROOT_DATASET_DECODE_STEP)
    ]
    still = (
        "so it would hand back its actions still normalised to -1..1, which the arm would take as "
        "degrees: serve a checkpoint saved with its statistics (upstream_api.GROOT_DECODE)"
    )
    if not decoders:
        raise PipelineRefused(
            f"{spec}'s post-processor has no step that decodes GR00T's actions, {still}"
        )
    for step in decoders:
        decode = step["config"]
        if step["registry_name"] == up.GROOT_DATASET_DECODE_STEP:
            if step["state_file"] is None or decode.get("normalize_min_max", True) is not True:
                raise PipelineRefused(
                    f"{spec}'s {up.GROOT_DATASET_DECODE_STEP} step keeps no statistics to "
                    f"unnormalise with, {still}"
                )
            continue
        raw, modalities = decode.get("raw_stats"), decode.get("modality_config")
        if not isinstance(raw, Mapping) or not isinstance(modalities, Mapping):
            raise PipelineRefused(
                f"{spec}'s {up.GROOT_DECODE_STEP} step names no raw_stats or modality_config, "
                f"{still}"
            )
        action = modalities.get("action")
        action = action if isinstance(action, Mapping) else {}
        groups, kinds = action.get("modality_keys"), action.get("action_configs")
        if not isinstance(groups, list) or not isinstance(kinds, list) or not groups:
            raise PipelineRefused(
                f"{spec}'s {up.GROOT_DECODE_STEP} step names no action groups to decode, {still}"
            )
        joints = "and quackd serves a GR00T whose every action group is joints (NON_EEF)"
        for n, group in enumerate(groups):
            kind = kinds[n] if n < len(kinds) and isinstance(kinds[n], Mapping) else {}
            said = str(kind.get("type")).lower()
            if said == up.GROOT_EEF:
                raise PipelineRefused(
                    f"{spec} decodes {str(group)[:80]} as an end-effector pose (EEF), {joints} "
                    "(upstream_api.GROOT_DECODE)"
                )
            if said != up.GROOT_NON_EEF:
                raise PipelineRefused(
                    f"{spec} gives its action group {str(group)[:80]} the type "
                    f"{str(kind.get('type'))[:40]!r}, which is not joints, {joints} "
                    "(upstream_api.GROOT_DECODE)"
                )
    raw, modalities = pack.get("raw_stats"), pack.get("modality_config")
    if not isinstance(raw, Mapping) or not isinstance(modalities, Mapping):
        return Family()
    return Family(
        state_quantiles=_grouped_quantiles(raw, modalities, "state"),
        action_quantiles=_grouped_quantiles(raw, modalities, "action"),
    )


def _grouped_quantiles(
    raw: Mapping[str, Any], modalities: Mapping[str, Any], modality: str
) -> wire.Quantiles | None:
    """The 1st and 99th percentiles of `modality`, its groups' joined in `modality_config`'s
    order as the pack step slices them (`upstream_api.GROOT_PERCENTILE_STATS`), or None where a
    group lacks either or they are not finite numbers."""
    section = modalities.get(modality)
    keys = section.get("modality_keys") if isinstance(section, Mapping) else None
    stats = raw.get(modality)
    if not isinstance(keys, list) or not keys or not isinstance(stats, Mapping):
        return None
    q01: list[float] = []
    q99: list[float] = []
    for key in keys:
        entry = stats.get(key)
        low, high = (
            entry.get(q) if isinstance(entry, Mapping) else None for q in up.QUANTILE_STATS
        )
        if not isinstance(low, list) or not isinstance(high, list) or len(low) != len(high):
            return None
        for value in (*low, *high):
            if isinstance(value, bool) or not isinstance(value, int | float):
                return None
        q01 += [float(v) for v in low]
        q99 += [float(v) for v in high]
    if len(q01) > wire.MAX_MOTORS or not all(math.isfinite(v) for v in q01 + q99):
        return None
    return wire.Quantiles(q01=q01, q99=q99)


def check_groot_base(
    hub: Hub, config: Mapping[str, Any], pins: Mapping[str, str], spec: str
) -> None:
    """A GR00T's pinned base, its `config.json` read before any weight: LeRobot builds the
    backbone from a config of its own only while the base names that backbone, and fetches any
    other as an AutoConfig trusting its repository's code, at no revision
    (`upstream_api.GROOT_BASE_MODEL`)."""
    base = str(config.get("base_model_path"))
    revision = pins[base]
    path = hub.file(base, revision, up.CONFIG_FILE)
    if path is None:
        raise PipelineRefused(
            f"{base}@{revision}, {spec}'s base_model_path, has no {up.CONFIG_FILE}, so it is not a "
            "GR00T N1.7 quackd can build from: pin nvidia/GR00T-N1.7-3B at a commit you have read"
        )
    said = _json(path, f"{base}@{revision}'s {up.CONFIG_FILE}")
    backbone = said.get("model_name", up.GROOT_BACKBONE) if isinstance(said, Mapping) else None
    if backbone != up.GROOT_BACKBONE:
        raise PipelineRefused(
            f"{base}@{revision}'s {up.CONFIG_FILE} names {str(backbone)[:80]!r} as its backbone, "
            "which LeRobot would fetch at no revision and load trusting its repository's code: "
            f"serve a GR00T whose base is built on {up.GROOT_BACKBONE}, whose config LeRobot "
            "carries (upstream_api.GROOT_BASE_MODEL)"
        )


# ── fetching ────────────────────────────────────────────────────────────────────────────


class Hub(Protocol):
    """Where a checkpoint's files come from: the Hugging Face Hub (`HubFetch`), or a test's."""

    def file(
        self, repo: str, revision: str, filename: str, *, dataset: bool = False
    ) -> Path | None:
        """One file of `repo` at `revision`, or None when the repository has no such file."""
        ...

    def snapshot(self, repo: str, revision: str, patterns: Sequence[str]) -> Path:
        """The files of `repo` at `revision` that match `patterns`, as a directory."""
        ...

    def tags(self, repo: str, *, dataset: bool = False) -> frozenset[str]:
        """The revisions that name a tag of `repo`, each by its name and by its full ref, and
        none a branch shares. A `PipelineRefused` when the Hub cannot be asked, whose words go
        on from a clause (`check_fixed`)."""
        ...


def _one_line(error: BaseException) -> str:
    text = " ".join(str(error).split()) or type(error).__name__
    return text if len(text) <= 300 else text[:297] + "..."


class HubFetch:
    """The Hub, through huggingface_hub's own cache, at a revision always. With `HF_HUB_OFFLINE`
    set it answers from that cache alone, a pinned model's snapshot included, which is how CI's
    torch job runs."""

    def file(
        self, repo: str, revision: str, filename: str, *, dataset: bool = False
    ) -> Path | None:
        from huggingface_hub import errors, hf_hub_download

        absent = getattr(errors, "RemoteEntryNotFoundError", errors.EntryNotFoundError)
        try:
            return Path(
                hf_hub_download(
                    repo,
                    filename,
                    revision=revision,
                    repo_type="dataset" if dataset else "model",
                )
            )
        except errors.LocalEntryNotFoundError:
            raise PipelineRefused(
                f"{filename} of {repo}@{revision} is not in this machine's Hub cache, and the "
                "Hub could not be asked for it (no network, or HF_HUB_OFFLINE is set)"
            ) from None
        except absent:
            return None
        except Exception as e:
            raise PipelineRefused(
                f"{filename} of {repo}@{revision} could not be fetched: {_one_line(e)}"
            ) from None

    def snapshot(self, repo: str, revision: str, patterns: Sequence[str]) -> Path:
        from huggingface_hub import constants, snapshot_download

        try:
            # offline, the cache's own files: left to itself, snapshot_download asks the Hub for
            # a commit's file list it has not cached, offline or not
            return Path(
                snapshot_download(
                    repo,
                    revision=revision,
                    allow_patterns=list(patterns),
                    local_files_only=constants.HF_HUB_OFFLINE,
                )
            )
        except Exception as e:
            raise PipelineRefused(
                f"{repo}@{revision} could not be fetched: {_one_line(e)}"
            ) from None

    def tags(self, repo: str, *, dataset: bool = False) -> frozenset[str]:
        from huggingface_hub import HfApi, errors

        try:
            refs = HfApi().list_repo_refs(repo, repo_type="dataset" if dataset else "model")
        except errors.OfflineModeIsEnabled:
            raise PipelineRefused(
                "HF_HUB_OFFLINE is set, so the Hub cannot be asked which of its revisions are tags"
            ) from None
        except Exception as e:
            raise PipelineRefused(
                f"the Hub could not say which of its revisions are tags: {_one_line(e)}"
            ) from None
        # a name that is a branch's as well as a tag's is refused, rather than guessed at
        branches = {branch.name for branch in refs.branches}
        return frozenset(
            name for tag in refs.tags if tag.name not in branches for name in (tag.name, tag.ref)
        )


def _landed(spec: str, directory: Path, revision: str) -> str:
    """`spec` as the record keeps it: with the commit its files landed at, where the Hub's
    cache names their directory by one and it is not the revision given, so a tag or a branch
    is recorded as the commit it was that day."""
    commit = directory.name
    if COMMIT.match(commit) and commit != revision:
        return f"{spec}, commit {commit[:12]}"
    return spec


def check_fixed(
    hub: Hub, repo: str, revision: str, what: str, instead: str, *, dataset: bool = False
) -> None:
    """Refuse `revision` of `repo` unless it is a commit or a tag, the revisions that name the
    same files from one day to the next. A branch names whatever was last pushed to it, and a
    pull request's ref the same, so what was read at one could change under you between two
    serves. A whole commit is known by its shape, and a tag only by asking the Hub (`Hub.tags`),
    so with the Hub out of reach only a commit is taken. `what` begins the sentence, and
    `instead` ends it with what to do."""
    if COMMIT.match(revision):
        return
    try:
        tagged = revision in hub.tags(repo, dataset=dataset)
    except PipelineRefused as e:
        raise PipelineRefused(
            f"{what} {repo}@{revision}, which is not a whole commit, and {e}: {instead}"
        ) from None
    if not tagged:
        raise PipelineRefused(
            f"{what} {repo}@{revision}, and {revision} is neither a whole commit nor one of its "
            f"tags, so what it names could change under you: {instead}"
        )


def _json(path: Path, what: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as e:
        raise PipelineRefused(f"{what} is not JSON quackd can read: {_one_line(e)}") from None


def check_pinned(directory: Path, spec: str) -> None:
    """A pinned model's directory as it is handed to the build, checked, or a refusal.

    What was fetched of it is `NESTED_FILES` at most, but the Hub's cache keeps one directory
    per commit, and it holds whatever was fetched at that commit before, a `.py` or a pickle
    included. So every file in it is one `NESTED_FILES` or `NESTED_DOCS` names, and no JSON in
    it maps a class to code (`CODE_MAP_KEY`), which is what would have transformers ask to run
    a repository's own code: SmolVLA's build asks for its backbone without saying
    `trust_remote_code` either way (`upstream_api.TOKENIZER_TRUSTS_REMOTE_CODE`). Nor does it
    hold a PEFT adapter (`ADAPTER_CONFIG`), whose files are JSON and safetensors as a model's
    are, and which the build would load onto the model, or swap the model for a base no pin
    names."""
    fresh = (
        "point HF_HUB_CACHE at a cache that has not fetched this revision before, or delete "
        f"{directory} and serve again"
    )
    for path in sorted(p for p in directory.rglob("*") if p.is_file()):
        name = path.relative_to(directory).as_posix()
        if not any(fnmatch.fnmatch(name, p) for p in NESTED_FILES + NESTED_DOCS):
            raise PipelineRefused(
                f"{spec} in this machine's Hub cache holds {name[:80]!r}, which is not a "
                "config, a tokenizer file or safetensors, and could be code or a pickle a "
                f"load would run: {fresh}"
            )
        if name == ADAPTER_CONFIG:
            raise PipelineRefused(
                f"{spec} holds a PEFT adapter ({name}), which transformers loads onto a model it "
                "builds from that directory, or, with no config.json beside it, loads the base "
                "model the adapter names in its place, which no pin fixes: pin a model with the "
                "adapter merged into its weights"
            )
        said = _json(path, f"{spec}'s {name}") if name.endswith(".json") else None
        if isinstance(said, Mapping) and CODE_MAP_KEY in said:
            raise PipelineRefused(
                f"{spec}'s {name} maps its classes to code ({CODE_MAP_KEY}), which loading it "
                "would ask to run: quackd loads a model transformers has the code for, and "
                "nothing a repository brings with it"
            )


def _required(hub: Hub, repo: str, revision: str, filename: str, *, beside: Path | None) -> Path:
    """A file the checkpoint must have, in the same directory as the rest of it."""
    path = hub.file(repo, revision, filename)
    if path is None:
        raise PipelineRefused(
            f"{repo}@{revision} has no {filename}, so it is not a LeRobot checkpoint quackd can "
            "serve: name the repository and revision `lerobot-train` pushed, or a copy of it"
        )
    if beside is not None and path.parent != beside:
        raise PipelineRefused(
            f"{filename} of {repo}@{revision} did not land beside its {up.CONFIG_FILE}, so the "
            "files would not be one checkpoint"
        )
    return path


def resolve_rate(
    fps: float | None, repo: str, revision: str, hub: Hub
) -> tuple[float, str, list[str]]:
    """The rate a checkpoint is served at, where it came from, and what was fetched to learn it.

    `--fps` when it is given. Otherwise the fps of the dataset `train_config.json` names, read
    from its `meta/info.json` at the revision the training run named (`upstream_api.
    TRAIN_DATASET`), since no config carries a rate (`upstream_api.CONFIG_HAS_NO_RATE`). A
    dataset named at no revision, or at a branch, is refused rather than read at whatever it is
    today (`check_fixed`)."""
    if fps is not None:
        return float(fps), "--fps", []
    spec = f"{repo}@{revision}"
    give = "give --fps, the fps of the dataset it learned from"
    path = hub.file(repo, revision, up.TRAIN_CONFIG_FILE)
    if path is None:
        raise PipelineRefused(
            f"{spec} has no {up.TRAIN_CONFIG_FILE} to say what data it learned from, and no "
            f"checkpoint carries a rate: {give}"
        )
    train = _json(path, f"{spec}'s {up.TRAIN_CONFIG_FILE}")
    dataset = train.get("dataset") if isinstance(train, Mapping) else None
    dataset = dataset if isinstance(dataset, Mapping) else {}
    name, at = dataset.get("repo_id"), dataset.get("revision")
    if not isinstance(name, str) or not REPO_ID.match(name):
        raise PipelineRefused(
            f"{spec} was trained on {str(name)[:80]!r}, which is not one dataset quackd can "
            f"read a rate from: {give}"
        )
    if not isinstance(at, str) or not re.match(r"^[\w.\-/]+$", at, re.ASCII):
        raise PipelineRefused(
            f"{spec} was trained on {name} at no revision, so the rate read from it could "
            f"change under you: {give}, which is in {name}'s {up.DATASET_INFO_FILE}"
        )
    if len(f"{name}@{at}") > MAX_SPEC_CHARS:
        raise PipelineRefused(f"{spec} names a dataset longer than a Hub id and a revision: {give}")
    check_fixed(
        hub,
        name,
        at,
        f"{spec} was trained on",
        f"{give}, which is in {name}'s {up.DATASET_INFO_FILE}",
        dataset=True,
    )
    info_path = hub.file(name, at, up.DATASET_INFO_FILE, dataset=True)
    if info_path is None:
        raise PipelineRefused(f"{name}@{at} has no {up.DATASET_INFO_FILE}: {give}")
    info = _json(info_path, f"{name}@{at}'s {up.DATASET_INFO_FILE}")
    rate = info.get("fps") if isinstance(info, Mapping) else None
    hz = math.nan
    if isinstance(rate, int | float) and not isinstance(rate, bool):
        # JSON reads an integer of any length, and a float holds less
        with contextlib.suppress(OverflowError):
            hz = float(rate)
    if not math.isfinite(hz):
        raise PipelineRefused(f"{name}@{at}'s {up.DATASET_INFO_FILE} has no fps: {give}")
    root = info_path
    for _ in Path(up.DATASET_INFO_FILE).parts:
        root = root.parent  # the directory the dataset's revision landed in
    landed = _landed(f"{name}@{at}", root, at)
    return hz, f"{name}@{at} {up.DATASET_INFO_FILE}", [f"dataset {landed}, its fps"]


@dataclass(frozen=True)
class Checkpoint:
    """A checkpoint's files, fetched and checked, and everything the build is to be told."""

    spec: str
    repo: str
    revision: str
    directory: Path
    shape: Shape
    device: str
    rate_hz: float
    rate_source: str
    pre_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)
    post_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)
    config_overrides: dict[str, Any] = field(default_factory=dict)
    loaded: tuple[str, ...] = ()
    state_quantiles: wire.Quantiles | None = None
    action_quantiles: wire.Quantiles | None = None
    """The percentiles the checkpoint's type keeps where the normaliser's are not (`Family`),
    which `LeRobotRunner` reports in their place, or None for the normaliser's."""


def _overrides(
    steps: Sequence[Mapping[str, Any]], *, device: str, nested: Mapping[str, Path], post: bool
) -> dict[str, dict[str, Any]]:
    """Each step's overrides, keyed by its registry name as LeRobot merges them over the saved
    config (`upstream_api.DEVICE_OVERRIDE`). The device step goes to the policy's device in the
    pre-processor and to the CPU in the post-processor, where the actions are read; any other
    step that keeps a device keeps the policy's; a step that could trust remote code does not;
    and a model a step names is the pinned directory it was fetched to."""
    out: dict[str, dict[str, Any]] = {}
    for step in steps:
        name, config = str(step["registry_name"]), step["config"]
        wanted: dict[str, Any] = {}
        if name == up.DEVICE_STEP:
            wanted["device"] = "cpu" if post else device
        elif "device" in config:
            wanted["device"] = device
        if TRUST_KEY in config:
            wanted[TRUST_KEY] = False
        for key in NESTED_STEP_KEYS:
            if isinstance(config.get(key), str) and config[key] in nested:
                wanted[key] = str(nested[config[key]])
        if wanted:
            out.setdefault(name, {}).update(wanted)
    return out


def fetch_checkpoint(
    spec: str,
    *,
    fps: float | None,
    pins: Sequence[str] = (),
    device: str,
    hub: Hub,
    importable: Callable[[str], bool] = importable_module,
) -> Checkpoint:
    """`spec` fetched and checked as the module's docstring says, in that order, or a refusal.
    Nothing is built here, and nothing that could run code is fetched before the JSON that
    says what the checkpoint is has been checked. `importable` says whether a package a type
    answers with would import here (`check_family`)."""
    repo, revision = parse_repo(spec, "--policy")
    spec = f"{repo}@{revision}"
    pinned = parse_pins(pins)
    config_path = _required(hub, repo, revision, up.CONFIG_FILE, beside=None)
    directory = config_path.parent
    config = _json(config_path, f"{spec}'s {up.CONFIG_FILE}")
    shape = check_config(config, spec, gpu=device != "cpu")
    steps: dict[str, list[dict[str, Any]]] = {}
    for filename in (up.PREPROCESSOR_FILE, up.POSTPROCESSOR_FILE):
        path = _required(hub, repo, revision, filename, beside=directory)
        doc = _json(path, f"{spec}'s {filename}")
        steps[filename] = check_processor(doc, filename, spec, shape.policy_type)
    every = steps[up.PREPROCESSOR_FILE] + steps[up.POSTPROCESSOR_FILE]
    family = check_family(config, every, spec, importable=importable)
    nested = nested_models(config, every, spec)
    pinned_models(nested, pinned, spec)
    for m in nested:
        check_fixed(
            hub,
            m,
            pinned[m],
            "--pin names",
            "pin a whole commit you have read, or a tag with the Hub in reach",
        )
    if shape.policy_type == "groot":
        check_groot_base(hub, config, pinned, spec)
    rate, source, rate_loaded = resolve_rate(fps, repo, revision, hub)
    # the code-free part is checked: now the weights, the processors' state and the models
    _required(hub, repo, revision, up.WEIGHTS_FILE, beside=directory)
    for step in every:
        if step["state_file"] is not None:
            _required(hub, repo, revision, step["state_file"], beside=directory)
    patterns = nested_patterns(config, every)
    fetched = {m: hub.snapshot(m, pinned[m], patterns[m]) for m in nested}
    for m, where in fetched.items():
        check_pinned(where, f"{m}@{pinned[m]}")
    config_overrides: dict[str, Any] = {"device": device}
    if shape.policy_type == "act":
        config_overrides["pretrained_backbone_weights"] = None  # up.ACT_BACKBONE_WEIGHTS
    for key in (key for keys in NESTED_CONFIG_KEYS.values() for key in keys):
        if isinstance(config.get(key), str) and config[key] in fetched:
            config_overrides[key] = str(fetched[config[key]])
    return Checkpoint(
        spec=spec,
        repo=repo,
        revision=revision,
        directory=directory,
        shape=shape,
        device=device,
        rate_hz=rate,
        rate_source=source,
        pre_overrides=_overrides(
            steps[up.PREPROCESSOR_FILE], device=device, nested=fetched, post=False
        ),
        post_overrides=_overrides(
            steps[up.POSTPROCESSOR_FILE], device=device, nested=fetched, post=True
        ),
        config_overrides=config_overrides,
        loaded=(
            f"checkpoint {_landed(spec, directory, revision)}",
            *rate_loaded,
            *(f"{nested[m]} {_landed(f'{m}@{pinned[m]}', fetched[m], pinned[m])}" for m in nested),
        ),
        state_quantiles=family.state_quantiles,
        action_quantiles=family.action_quantiles,
    )


def camera_map(given: Mapping[str, str], shape: Shape, spec: str) -> dict[str, str]:
    """Which of the arm's cameras is which of the checkpoint's images: `--cameras` where it is
    given, every key it names being one of the checkpoint's, and otherwise each image's own
    name, `observation.images.front` from the camera called `front` (`upstream_api.
    FEATURE_KEYS`)."""
    keys = shape.image_keys
    if not given:
        return {
            key.removeprefix(up.IMAGES_PREFIX): key
            for key in keys
            if key.startswith(up.IMAGES_PREFIX)
            and re.match(wire.NAME_PATTERN, key.removeprefix(up.IMAGES_PREFIX))
        }
    unknown = [f"{name}={key}" for name, key in given.items() if key not in keys]
    if unknown:
        raise PipelineRefused(
            f"--cameras maps {', '.join(unknown)}, and {spec} looks at "
            f"{', '.join(keys) or 'no image'}"
        )
    if len(set(given.values())) != len(given):
        raise PipelineRefused("--cameras maps two cameras to one of the policy's images")
    return dict(given)


# ── running it ──────────────────────────────────────────────────────────────────────────


def _quantiles(steps: Sequence[Any], key: str) -> wire.Quantiles | None:
    """`key`'s 1st and 99th percentiles, from whichever step keeps them
    (`upstream_api.NORMALIZER_STATS`), or None where none does or they are not finite."""
    low_name, high_name = (f"{key}.{stat}" for stat in up.QUANTILE_STATS)
    for step in steps:
        state = step.state_dict() if callable(getattr(step, "state_dict", None)) else {}
        low, high = state.get(low_name), state.get(high_name)
        if low is None or high is None:
            continue
        q01 = [float(v) for v in low.detach().cpu().reshape(-1).tolist()]
        q99 = [float(v) for v in high.detach().cpu().reshape(-1).tolist()]
        if len(q01) != len(q99) or not all(math.isfinite(v) for v in q01 + q99):
            return None
        return wire.Quantiles(q01=q01, q99=q99)
    return None


class LeRobotRunner:
    """A checkpoint as the policy server runs it (a `PolicyRunner`), for one session's arm at a
    time and one step at a time, under its own lock.

    `begin` shapes the steps for the arm a session's reset declared: its motors in its bus's
    order name the state and the action, and each camera it gives is handed to the policy under
    the image the server maps it to. `chunk` is `n_action_steps`, what one step answers with,
    and `threads`, `gpu`, `loaded` and the quantiles are what `/v1/policy` reports."""

    def __init__(
        self,
        checkpoint: Checkpoint,
        *,
        policy: Any,
        preprocessor: Any,
        postprocessor: Any,
        cameras: Mapping[str, str],
        threads: int,
        latency_s: float,
    ) -> None:
        self.checkpoint = checkpoint
        self.shape = checkpoint.shape
        self.chunk = checkpoint.shape.n_action_steps
        self.chunk_size = checkpoint.shape.chunk_size
        self.cameras = dict(cameras)
        self.threads = threads
        self.gpu = checkpoint.device != "cpu"
        self.loaded = checkpoint.loaded
        # a type that keeps its percentiles elsewhere says so in the checkpoint (`Family`)
        self.state_quantiles = (
            checkpoint.state_quantiles
            if checkpoint.state_quantiles is not None
            else _quantiles(preprocessor.steps, up.STATE_KEY)
        )
        self.action_quantiles = (
            checkpoint.action_quantiles
            if checkpoint.action_quantiles is not None
            else _quantiles([*preprocessor.steps, *postprocessor.steps], up.ACTION_KEY)
        )
        self._policy = policy
        self._pre = preprocessor
        self._post = postprocessor
        self._latency_s = latency_s
        self._lock = threading.Lock()
        self._task = ""
        self._features: dict[str, dict[str, Any]] | None = None
        self._frames: dict[str, str] = {}
        self._closed = False
        """Set by `close`, after which no session begins and no step is answered."""

    def policy_features(self) -> wire.PolicyFeatures:
        return wire.PolicyFeatures(
            state=self.shape.state,
            action=self.shape.action,
            images=[wire.ImageFeature(key=k, height=h, width=w) for k, h, w in self.shape.images],
            action_names=list(self.shape.action_names) if self.shape.action_names else None,
            pads_images=self.shape.pads_images,
        )

    def begin(self, motors: Sequence[str], cameras: Mapping[str, wire.CameraInfo]) -> None:
        """The next session's arm: `motors` in its bus's order, and the cameras it declared.
        Joint order is trusted, not checked: the checkpoint's state and action are taken to be
        in the order of the bus that recorded its data, which is how LeRobot records one."""
        names = [f"{motor}.pos" for motor in motors]
        vector = {"dtype": "float32", "shape": (len(names),), "names": names}
        features: dict[str, dict[str, Any]] = {up.STATE_KEY: vector, up.ACTION_KEY: dict(vector)}
        frames: dict[str, str] = {}
        for camera, key in self.cameras.items():
            info = cameras.get(camera)
            if info is None:
                continue  # a policy that pads it is shown the rest, and one that does not refused
            features[key] = {
                "dtype": "video",
                "shape": (info.height, info.width, 3),
                "names": ["height", "width", "channels"],
            }
            frames[camera] = key.removeprefix(up.IMAGES_PREFIX)
        with self._lock:
            if self._closed:
                # the features `close` took away would come back, and a step with them
                raise RuntimeError("the policy was closed, and begins no session")
            self._features, self._frames = features, frames

    def reset(self, instruction: str) -> None:
        with self._lock:
            # the policy's queue and every processor's state (up.POLICY_RESET, PROCESSOR_RESET)
            self._policy.reset()
            self._pre.reset()
            self._post.reset()
            self._task = instruction

    def features(self) -> Features:
        return Features(
            self.checkpoint.rate_hz, self.checkpoint.rate_source, per_tick=self.shape.per_tick
        )

    def latency_s(self) -> float:
        return self._latency_s

    def next_chunk(self, observation: Observation, sent: Mapping[str, float]) -> Chunk:
        import torch
        from lerobot.policies.utils import build_inference_frame, make_robot_action

        with self._lock:
            features = self._features
            if self._closed:
                raise RuntimeError("the policy was closed, and answers no step")
            if features is None:
                raise RuntimeError("the policy was asked for a step before a session began")
            reading = observation.reading
            raw: dict[str, Any] = {n: float(reading[n]) for n in features[up.STATE_KEY]["names"]}
            for camera, name in self._frames.items():
                raw[name] = np.array(reading[camera], dtype=np.uint8, copy=True)
            with torch.inference_mode():
                frame = build_inference_frame(
                    raw, torch.device(self.checkpoint.device), features, task=self._task
                )
                batch = self._pre(frame)
                if self.shape.per_tick:
                    rows = [self._post(self._policy.select_action(batch))]
                else:
                    chunk = self._policy.predict_action_chunk(batch)
                    if chunk.ndim == 2:
                        chunk = chunk.unsqueeze(0)
                    chunk = self._post(chunk[:, : self.chunk])
                    rows = [chunk[:, i] for i in range(chunk.shape[1])]
                actions = tuple(
                    {name.removesuffix(".pos"): value for name, value in action.items()}
                    for action in (make_robot_action(row, features) for row in rows)
                )
        return Chunk(observation.tick, actions)

    def close(self) -> None:
        # not under the lock, which a step holds for as long as torch takes: a server being
        # stopped waits on no inference (`server.CLOSE_WAIT_S`). A step already inferring has
        # the features it runs on, and every begin and step after this is refused.
        self._closed = True
        self._features = None


@functools.cache
def torch_threads() -> int:
    """How many threads torch takes on its own in this process, asked once, before `load` sets
    any: asked again after, it would say what the last load set, and each load would take one
    thread fewer than the one before it."""
    import torch

    return int(torch.get_num_threads())


def build_policy(policy_class: Any, where: Path, config: Any) -> Any:
    """The policy `config` describes, with every weight the checkpoint in `where` holds loaded
    into it and none left as it was built, or the error that says why not.

    LeRobot's own loader only logs a weight the file lacks or one it has that the model does
    not (`upstream_api.POLICY_FROM_PRETRAINED`), so it is asked to be strict. pi05's and pi0's
    go further and hand back the model with none of its weights when they do not load
    (`upstream_api.PI05_FROM_PRETRAINED`, `PI0_FROM_PRETRAINED`), so their weights are loaded
    here as those loaders load them, their own key fixes and all, with nothing caught."""
    if config.type not in LENIENT_LOADERS:
        return policy_class.from_pretrained(str(where), config=config, strict=True)
    from safetensors.torch import load_file

    policy = policy_class(config)
    weights = policy._fix_pytorch_state_dict_keys(
        load_file(str(where / up.WEIGHTS_FILE)), policy.config
    )
    policy.load_state_dict(
        {k if k.startswith(MODEL_PREFIX) else MODEL_PREFIX + k: v for k, v in weights.items()},
        strict=True,
    )
    policy.to(config.device)
    policy.eval()
    return policy


def load(
    spec: str,
    *,
    fps: float | None,
    pins: Sequence[str] = (),
    cameras: Mapping[str, str] | None = None,
    threads: int | None = None,
    latency_s: float = 0.0,
    hub: Hub | None = None,
) -> LeRobotRunner:
    """The checkpoint `spec` names, fetched, checked and built, as a runner the policy server
    serves, or a `PipelineRefused` that says why not. The device is the one LeRobot picks, and
    torch gets `threads` threads, or one fewer than it would take on its own (`torch_threads`),
    so the machine has a core left for everything else it runs. The weights are loaded strictly
    (`build_policy`), so a checkpoint whose weights are not its model's is refused rather than
    served as the random network it would be."""
    try:
        import torch
        from lerobot.configs.policies import PreTrainedConfig
        from lerobot.policies.factory import get_policy_class, make_pre_post_processors
        from lerobot.utils.device_utils import auto_select_torch_device
    except ImportError as e:
        raise PipelineRefused(
            f"serving {spec} needs torch and LeRobot, which quackd[lerobot-vla] installs on "
            f"Python 3.12 ({type(e).__name__}: {e})"
        ) from None
    device = auto_select_torch_device().type
    checkpoint = fetch_checkpoint(spec, fps=fps, pins=pins, device=device, hub=hub or HubFetch())
    mapped = camera_map(cameras or {}, checkpoint.shape, checkpoint.spec)
    count = threads if threads is not None else max(1, torch_threads() - 1)
    torch.set_num_threads(count)
    where = str(checkpoint.directory)
    try:
        config = PreTrainedConfig.from_pretrained(where)
        for key, value in checkpoint.config_overrides.items():
            setattr(config, key, value)
        policy = build_policy(get_policy_class(config.type), checkpoint.directory, config)
        pre, post = make_pre_post_processors(
            config,
            pretrained_path=where,
            preprocessor_overrides=checkpoint.pre_overrides,
            postprocessor_overrides=checkpoint.post_overrides,
        )
    except Exception as e:
        raise PipelineRefused(f"{checkpoint.spec} did not load: {_one_line(e)}") from None
    return LeRobotRunner(
        checkpoint,
        policy=policy,
        preprocessor=pre,
        postprocessor=post,
        cameras=mapped,
        threads=count,
        latency_s=latency_s,
    )


__all__ = [
    "STEPS_BY_TYPE",
    "Checkpoint",
    "Family",
    "Hub",
    "HubFetch",
    "LeRobotRunner",
    "PipelineRefused",
    "Shape",
    "build_policy",
    "camera_map",
    "check_config",
    "check_family",
    "check_fixed",
    "check_groot_base",
    "check_pinned",
    "check_processor",
    "fetch_checkpoint",
    "load",
    "nested_models",
    "nested_patterns",
    "parse_pins",
    "parse_repo",
    "pinned_models",
    "resolve_rate",
    "torch_threads",
]
