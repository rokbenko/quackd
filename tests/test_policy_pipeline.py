"""The LeRobot pipeline the policy server loads a checkpoint through, and a checkpoint run in it.

Two halves. The first reads what a checkpoint says about itself, with no torch anywhere: the
JSON it is checked by, the order it is fetched in so that nothing that could run code arrives
before the JSON that names it has been checked, the models it names inside itself, where its
rate comes from, and what the build is told. These run in the default suite, over a fake Hub.

The second is CI's torch job (`.github/workflows/ci.yml`, `policy`): a tiny random ACT, built
here with no pretrained backbone and saved naming LeRobot's default one, both processors saved,
put in a Hub cache of its own at a commit and a tag, and served with the Hub and torchvision
offline by the real server to the real client and an arm behind it, and a tiny random X-VLA
served the same way, with a tokenizer of a few words pinned at a commit. It is skipped where
torch, LeRobot or transformers is missing, unless `QUACKD_REQUIRE_TORCH` is set, as it is in
that job, where a missing one fails the run rather than leaving it green having loaded nothing.
It is what moves POLICY_PIPELINE to VERIFIED (`policy/upstream_api.py`), for those two on the
CPU and nothing else, and a random policy says nothing about what a trained one does to an arm.
"""

from __future__ import annotations

import dataclasses
import hashlib
import importlib.util
import json
import math
import os
import re
import threading
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from typer.testing import CliRunner

from quackd.cli import app
from quackd.safety import Executor, allow_all
from quackd.verbs.registry import registry_from_manifest
from quackd_lerobot import LeRobotAdapter
from quackd_lerobot.policy import pipeline as P
from quackd_lerobot.policy import protocol as wire
from quackd_lerobot.policy import server as S
from quackd_lerobot.policy import upstream_api as up
from quackd_lerobot.policy.client import RemoteRunner
from quackd_lerobot.policy.runner import Observation
from quackd_lerobot.real import LeRobotReal, joint_ranges, parse_camera_url
from quackd_lerobot.verbs import JOINTS, RAN, TICK_S
from tests.test_lerobot_adapter import FakeCamera, _segment_arm
from tests.test_policy_loop import STEP, LockstepClock

TOKEN = "5f0c2a8e9d7b41c3a6e8f0d2b4c6a8e0f1d3b5c7a9e1f3d5b7c9a1e3f5d7b9c1"
REPO = "quackd-test/tiny-act"
DATASET = "quackd-test/tiny-data"
TAG = "v1"
"""The tag the checkpoint and its dataset are also cached under, beside their commits."""
FPS = round(1.0 / TICK_S)
"""The rate the tiny ACT's dataset says it was recorded at: the verbs' own tick, and nothing any
arm was measured at."""
FRAME_H, FRAME_W = (int(n) for n in FakeCamera().read_latest().shape[:2])
"""The size of the frames the test suite's camera gives, which the tiny ACT is built to see."""
CHUNK_SIZE, N_ACTION_STEPS = 8, 4
"""A chunk short enough to build fast, and fewer played of it than predicted, so the cut to
`n_action_steps` is visible."""


def _commit(name: str) -> str:
    """A commit hash for `name`, shaped as the Hub's are, and nobody's."""
    return hashlib.sha1(name.encode("utf-8")).hexdigest()


PINNED = _commit("SomeOrg/Some-VLM")
"""The commit a SmolVLA-shaped checkpoint's backbone is pinned at."""


# ── a fake Hub, for reading what a checkpoint says with no torch ────────────────────────


class FakeHub:
    """Files by (repo, revision), written out as they are asked for, and every ask kept in
    order, so a test can say what was fetched before what. Each repository's tags are
    `TAG` alone, unless a test says otherwise, and `unreachable` is why the Hub cannot be asked
    for them, when it cannot."""

    def __init__(self, root: Path, files: Mapping[tuple[str, str], Mapping[str, Any]]) -> None:
        self.root = root
        self.files = {k: dict(v) for k, v in files.items()}
        self.asked: list[tuple[str, str, str]] = []
        self.snapshots: list[tuple[str, str, tuple[str, ...]]] = []
        self.tagged: dict[str, frozenset[str]] = {}
        self.tags_asked: list[str] = []
        self.unreachable: str | None = None

    def tags(self, repo: str, *, dataset: bool = False) -> frozenset[str]:
        self.tags_asked.append(repo)
        if self.unreachable is not None:
            raise P.PipelineRefused(self.unreachable)
        return self.tagged.get(repo, frozenset({TAG}))

    def file(
        self, repo: str, revision: str, filename: str, *, dataset: bool = False
    ) -> Path | None:
        self.asked.append((repo, revision, filename))
        content = self.files.get((repo, revision), {}).get(filename)
        if content is None:
            return None
        path = self.root / repo.replace("/", "--") / revision / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(content) if not isinstance(content, str) else content)
        return path

    def snapshot(self, repo: str, revision: str, patterns: Sequence[str]) -> Path:
        self.snapshots.append((repo, revision, tuple(patterns)))
        path = self.root / "snapshots" / repo.replace("/", "--") / revision
        path.mkdir(parents=True, exist_ok=True)
        return path


def _act_config(**over: Any) -> dict[str, Any]:
    """An ACT's `config.json` as LeRobot saves one, for a six-motor arm and a front camera."""
    n = len(JOINTS)
    return {
        "type": "act",
        "input_features": {
            up.STATE_KEY: {"type": "STATE", "shape": [n]},
            f"{up.IMAGES_PREFIX}front": {"type": "VISUAL", "shape": [3, FRAME_H, FRAME_W]},
        },
        "output_features": {up.ACTION_KEY: {"type": "ACTION", "shape": [n]}},
        "device": "cuda",
        "chunk_size": CHUNK_SIZE,
        "n_action_steps": N_ACTION_STEPS,
        "pretrained_backbone_weights": "ResNet18_Weights.IMAGENET1K_V1",
        "temporal_ensemble_coeff": None,
        **over,
    }


def _processors(extra_pre: Sequence[Mapping[str, Any]] = ()) -> tuple[dict[str, Any], ...]:
    """ACT's two processor JSONs as LeRobot saves them, with `extra_pre` steps added."""
    rename, batch, device, normalizer, unnormalizer = up.DEFAULT_STEP_NAMES
    pre = {
        "name": "policy_preprocessor",
        "steps": [
            {"registry_name": rename, "config": {"rename_map": {}}},
            {"registry_name": batch, "config": {}},
            *extra_pre,
            {"registry_name": device, "config": {"device": "cuda", "float_dtype": None}},
            {
                "registry_name": normalizer,
                "config": {"eps": 1e-8, "device": "cuda"},
                "state_file": f"policy_preprocessor_step_3_{normalizer}.safetensors",
            },
        ],
    }
    post = {
        "name": "policy_postprocessor",
        "steps": [
            {
                "registry_name": unnormalizer,
                "config": {"eps": 1e-8},
                "state_file": f"policy_postprocessor_step_0_{unnormalizer}.safetensors",
            },
            {"registry_name": device, "config": {"device": "cpu", "float_dtype": None}},
        ],
    }
    return pre, post


def _hub(
    tmp_path: Path, *, config: Any = None, pre: Any = None, post: Any = None, **more: Any
) -> FakeHub:
    """A Hub holding one ACT checkpoint at a commit, and its dataset's `meta/info.json`."""
    default_pre, default_post = _processors()
    files: dict[str, Any] = {
        up.CONFIG_FILE: _act_config() if config is None else config,
        up.PREPROCESSOR_FILE: default_pre if pre is None else pre,
        up.POSTPROCESSOR_FILE: default_post if post is None else post,
        up.WEIGHTS_FILE: "weights",
        up.TRAIN_CONFIG_FILE: {"dataset": {"repo_id": DATASET, "revision": TAG}},
        **more,
    }
    for processor in (files[up.PREPROCESSOR_FILE], files[up.POSTPROCESSOR_FILE]):
        for step in processor.get("steps", []) if isinstance(processor, dict) else []:
            if isinstance(step, dict) and isinstance(step.get("state_file"), str):
                files[step["state_file"]] = "state"
    files = {k: v for k, v in files.items() if v is not None}
    return FakeHub(
        tmp_path,
        {(REPO, _commit(REPO)): files, (DATASET, TAG): {up.DATASET_INFO_FILE: {"fps": FPS}}},
    )


def _fetch(hub: FakeHub, **kw: Any) -> P.Checkpoint:
    return P.fetch_checkpoint(
        f"{REPO}@{_commit(REPO)}",
        fps=kw.pop("fps", None),
        device=kw.pop("device", "cpu"),
        hub=hub,
        **kw,
    )


# ── what a checkpoint says, read with no torch ──────────────────────────────────────────


def test_a_checkpoint_is_read_before_anything_that_could_run_is_fetched(tmp_path: Path) -> None:
    """The config and both processor JSONs first, then the training run's dataset for the rate,
    and only then the weights and the processors' state. Every repository fetched is named with
    its revision, which is what `/v1/policy` says and the arm's record keeps."""
    hub = _hub(tmp_path)
    checkpoint = _fetch(hub)
    names = [filename for _, _, filename in hub.asked]
    assert names[:3] == [up.CONFIG_FILE, up.PREPROCESSOR_FILE, up.POSTPROCESSOR_FILE]
    assert names.index(up.WEIGHTS_FILE) > names.index(up.DATASET_INFO_FILE)
    assert all(name.endswith(".safetensors") for name in names[names.index(up.WEIGHTS_FILE) :])
    assert checkpoint.rate_hz == FPS and DATASET in checkpoint.rate_source
    assert checkpoint.loaded == (
        f"checkpoint {REPO}@{_commit(REPO)}",
        f"dataset {DATASET}@{TAG}, its fps",
    )
    assert checkpoint.shape.state == len(JOINTS) and not checkpoint.shape.per_tick
    assert (checkpoint.shape.chunk_size, checkpoint.shape.n_action_steps) == (
        CHUNK_SIZE,
        N_ACTION_STEPS,
    )
    # the build is told to fetch no backbone, and where each processor's device step goes
    assert checkpoint.config_overrides == {"device": "cpu", "pretrained_backbone_weights": None}
    device, normalizer = up.DEVICE_STEP, up.DEFAULT_STEP_NAMES[3]
    assert checkpoint.pre_overrides == {device: {"device": "cpu"}, normalizer: {"device": "cpu"}}
    assert checkpoint.post_overrides == {device: {"device": "cpu"}}
    on_gpu = _fetch(_hub(tmp_path / "gpu"), device="cuda")
    assert on_gpu.pre_overrides[device] == {"device": "cuda"}
    assert on_gpu.post_overrides[device] == {"device": "cpu"}, "the answers are read on the CPU"


@pytest.mark.parametrize(
    ("step", "needle"),
    [
        ({"class": "evil.module.Step", "config": {}}, "named by its class"),
        (
            {"registry_name": up.DEFAULT_STEP_NAMES[0], "class": "evil.Step", "config": {}},
            "named by its class",
        ),
        (
            {"registry_name": "action_tokenizer_processor", "config": {}},
            "turns actions into tokens",
        ),
        ({"registry_name": "some_step_of_its_own", "config": {}}, "not one of"),
        ({"registry_name": [up.DEVICE_STEP], "config": {}}, "not one of"),
        ({"registry_name": {"name": up.DEVICE_STEP}, "config": {}}, "not one of"),
        (
            {"registry_name": up.DEFAULT_STEP_NAMES[3], "state_file": "../../x.safetensors"},
            "not a .safetensors file beside it",
        ),
        (
            {"registry_name": up.DEFAULT_STEP_NAMES[3], "state_file": "state.pickle"},
            "not a .safetensors file beside it",
        ),
    ],
)
def test_a_step_that_is_code_the_checkpoint_chose_is_refused_before_its_weights(
    tmp_path: Path, step: dict[str, Any], needle: str
) -> None:
    pre, post = _processors(extra_pre=[step])
    hub = _hub(tmp_path, pre=pre, post=post)
    with pytest.raises(P.PipelineRefused, match=needle):
        _fetch(hub, fps=float(FPS))
    assert up.WEIGHTS_FILE not in [filename for _, _, filename in hub.asked]


def test_a_step_that_could_trust_remote_code_is_told_not_to(tmp_path: Path) -> None:
    tokenizer = "tokenizer_processor"
    assert tokenizer in up.PI05_STEP_NAMES
    pre, post = _processors(extra_pre=[{"registry_name": tokenizer, "config": {P.TRUST_KEY: True}}])
    hub = _hub(tmp_path, config=_act_config(type="pi05"), pre=pre, post=post)
    checkpoint = _fetch(hub, fps=float(FPS))
    assert checkpoint.pre_overrides[tokenizer] == {P.TRUST_KEY: False}


@pytest.mark.parametrize(
    ("policy_type", "step"),
    [
        ("act", "tokenizer_processor"),
        ("act", "relative_actions_processor"),
        ("smolvla", "pi05_prepare_state_tokenizer_processor_step"),
        ("pi05", "smolvla_new_line_processor"),
    ],
)
def test_a_step_is_allowed_by_the_type_that_names_it_and_no_other(
    tmp_path: Path, policy_type: str, step: str
) -> None:
    """Every step another type's processors use is one LeRobot registers, and one quackd read for
    that type alone: an ACT naming a tokenizer is code nobody read for an ACT. The refusal says
    what the type does allow, before any weight is fetched."""
    assert any(step in allowed for allowed in P.STEPS_BY_TYPE.values()), step
    assert step not in P.STEPS_BY_TYPE[policy_type]
    pre, post = _processors(extra_pre=[{"registry_name": step, "config": {}}])
    hub = _hub(tmp_path, config=_act_config(type=policy_type), pre=pre, post=post)
    with pytest.raises(P.PipelineRefused, match=f"allows for a {policy_type!r} policy") as refused:
        _fetch(hub, fps=float(FPS))
    assert str(refused.value).endswith(", ".join(sorted(P.STEPS_BY_TYPE[policy_type])))
    assert up.WEIGHTS_FILE not in [filename for _, _, filename in hub.asked]


def test_a_tokenizer_a_step_names_is_fetched_without_its_weights(tmp_path: Path) -> None:
    """A model a processor step names is a tokenizer, and a tokenizer loads its configs and its
    vocabulary and never a weight, so its weights are not fetched: a pi05 used to fetch every
    safetensors file of the gated model whose tokenizer it uses. A model `config.json` names is
    one the policy is built from, SmolVLA's backbone, and its weights come with it, so a
    repository named both ways is fetched whole, and one named only by the step is not."""
    tokenizer = "SomeOrg/Some-Tokenizer"
    pre, post = _processors(
        extra_pre=[
            {"registry_name": "tokenizer_processor", "config": {"tokenizer_name": tokenizer}}
        ]
    )
    hub = _hub(tmp_path / "pi05", config=_act_config(type="pi05"), pre=pre, post=post)
    _fetch(hub, fps=float(FPS), pins=(f"{tokenizer}@{PINNED}",))
    ((repo, _, patterns),) = hub.snapshots
    assert repo == tokenizer and patterns == P.TOKENIZER_FILES
    assert not any(p.endswith(".safetensors") for p in patterns), patterns
    hub = _smolvla(tmp_path / "smolvla")
    _fetch(hub, fps=float(FPS), pins=(f"SomeOrg/Some-VLM@{PINNED}",))
    ((_, _, patterns),) = hub.snapshots
    assert patterns == P.NESTED_FILES and "*.safetensors" in patterns
    newline = up.SMOLVLA_STEP_NAMES[0]
    pre, post = _processors(
        extra_pre=[
            {"registry_name": newline, "config": {}},
            {"registry_name": "tokenizer_processor", "config": {"tokenizer_name": tokenizer}},
        ]
    )
    config = _act_config(type="smolvla", vlm_model_name="SomeOrg/Some-VLM")
    hub = _hub(tmp_path / "apart", config=config, pre=pre, post=post)
    _fetch(hub, fps=float(FPS), pins=(f"SomeOrg/Some-VLM@{PINNED}", f"{tokenizer}@{PINNED}"))
    assert {repo: patterns for repo, _, patterns in hub.snapshots} == {
        "SomeOrg/Some-VLM": P.NESTED_FILES,
        tokenizer: P.TOKENIZER_FILES,
    }


def _smolvla(tmp_path: Path) -> FakeHub:
    """A SmolVLA-shaped checkpoint that names its backbone in its config and its tokenizer in a
    step, the same repository both times, as SmolVLA's processor factory writes it."""
    backbone = "SomeOrg/Some-VLM"
    newline, tokenizer = up.SMOLVLA_STEP_NAMES
    pre, post = _processors(
        extra_pre=[
            {"registry_name": newline, "config": {}},
            {"registry_name": tokenizer, "config": {"tokenizer_name": backbone}},
        ]
    )
    return _hub(
        tmp_path, config=_act_config(type="smolvla", vlm_model_name=backbone), pre=pre, post=post
    )


def test_a_model_the_checkpoint_names_inside_itself_is_pinned_or_refused(tmp_path: Path) -> None:
    hub = _smolvla(tmp_path)
    with pytest.raises(P.PipelineRefused, match=r"--pin SomeOrg/Some-VLM@REVISION"):
        _fetch(hub, fps=float(FPS))
    assert up.WEIGHTS_FILE not in [filename for _, _, filename in hub.asked]
    pinned = _fetch(hub, fps=float(FPS), pins=(f"SomeOrg/Some-VLM@{PINNED}",))
    ((repo, revision, patterns),) = hub.snapshots
    assert (repo, revision) == ("SomeOrg/Some-VLM", PINNED)
    assert "*.py" not in patterns and not any(p.endswith((".bin", ".pt", ".pkl")) for p in patterns)
    where = str(hub.snapshot(repo, revision, patterns))
    assert pinned.config_overrides["vlm_model_name"] == where
    assert pinned.pre_overrides[up.SMOLVLA_STEP_NAMES[1]] == {"tokenizer_name": where}
    assert f"vlm_model_name SomeOrg/Some-VLM@{PINNED}" in pinned.loaded
    assert pinned.shape.pads_images
    for pins, needle in (
        ((f"SomeOrg/Some-VLM@{PINNED}", "Other/Model@def"), "names no such model"),
        ((f"SomeOrg/Some-VLM@{PINNED}", "SomeOrg/Some-VLM@def456"), f"at {PINNED} and at def456"),
        (("SomeOrg/Some-VLM",), "REPO@REVISION"),
    ):
        with pytest.raises(P.PipelineRefused, match=needle):
            _fetch(hub, fps=float(FPS), pins=pins)
    with pytest.raises(P.PipelineRefused, match="not a Hub repository"):
        _fetch(
            _hub(
                tmp_path / "path", config=_act_config(type="smolvla", vlm_model_name="C:/models/x")
            ),
            fps=float(FPS),
        )


def test_a_smolvla_that_names_no_backbone_is_refused_before_its_weights(tmp_path: Path) -> None:
    """LeRobot fills a SmolVLA config's missing backbone with a default of its own, a Hub name
    it would load at no revision, and nothing in the checkpoint would ask for a pin."""
    hub = _hub(tmp_path, config=_act_config(type="smolvla"))
    with pytest.raises(P.PipelineRefused, match="names no vlm_model_name") as refused:
        _fetch(hub, fps=float(FPS))
    assert "--pin REPO@REVISION" in str(refused.value)
    assert up.WEIGHTS_FILE not in [filename for _, _, filename in hub.asked]
    assert not hub.snapshots


@pytest.mark.parametrize(
    ("name", "content", "needle"),
    [
        ("modeling_vlm.py", "import os", "could be code or a pickle"),
        ("pytorch_model.bin", "pickled", "could be code or a pickle"),
        ("onnx/model.onnx", "graph", "could be code or a pickle"),
        (
            "config.json",
            json.dumps({"auto_map": {"AutoConfig": "Other/Repo--code.Config"}}),
            "maps its classes to code",
        ),
        (
            "tokenizer_config.json",
            json.dumps({"auto_map": {"AutoTokenizer": ["code.Tokenizer", None]}}),
            "maps its classes to code",
        ),
    ],
)
def test_a_pinned_model_whose_cached_directory_holds_code_is_refused(
    tmp_path: Path, name: str, content: str, needle: str
) -> None:
    """What is fetched of a pinned model is configs, tokenizer files and safetensors, but the
    Hub's cache keeps one directory per commit, and it may hold what an earlier fetch left
    there. A file of any other kind in it, or a config mapping a class to code, refuses the
    load before anything is built from it."""
    hub = _smolvla(tmp_path)
    where = hub.snapshot("SomeOrg/Some-VLM", PINNED, ())
    for fine, said in (
        ("config.json", "{}"),
        ("tokenizer.json", "{}"),
        ("merges.txt", ""),
        ("README.md", ""),
        (".gitattributes", ""),
        ("model.safetensors", ""),
    ):
        (where / fine).write_text(said)
    pinned = _fetch(hub, fps=float(FPS), pins=(f"SomeOrg/Some-VLM@{PINNED}",))
    assert pinned.config_overrides["vlm_model_name"] == str(where)
    (where / name).parent.mkdir(parents=True, exist_ok=True)
    (where / name).write_text(content)
    with pytest.raises(P.PipelineRefused, match=needle):
        _fetch(hub, fps=float(FPS), pins=(f"SomeOrg/Some-VLM@{PINNED}",))


def test_a_pi05_that_learned_relative_actions_is_served_in_chunks(tmp_path: Path) -> None:
    """A pi05's relative actions are made absolute over the whole chunk, against the state it
    was predicted from, as its training made them relative: it is not a policy asked every
    tick, whatever LeRobot's own loop does with one (`upstream_api.VLA_PIPELINE`)."""
    relative, absolute = "relative_actions_processor", "absolute_actions_processor"
    assert {relative, absolute} <= set(up.PI05_STEP_NAMES)
    pre, post = _processors(extra_pre=[{"registry_name": relative, "config": {"enabled": True}}])
    post["steps"].insert(1, {"registry_name": absolute, "config": {"enabled": True}})
    checkpoint = _fetch(_hub(tmp_path, config=_act_config(type="pi05"), pre=pre, post=post))
    assert not checkpoint.shape.per_tick and checkpoint.shape.pads_images
    assert checkpoint.shape.n_action_steps == N_ACTION_STEPS
    assert "pretrained_backbone_weights" not in checkpoint.config_overrides


PALIGEMMA = "google/paligemma-3b-pt-224"
"""The gated tokenizer pi0's and pi05's processors name, as LeRobot's own factories write it."""


def _pi0(tmp_path: Path, *, relative: bool = False) -> FakeHub:
    """A pi0 as the SO-101 fine-tunes on the Hub were saved on 2026-10-25: six motors named in
    `action_feature_names`, a top and a wrist camera, chunks of 50, PaliGemma's tokenizer named
    in its step, and the relative and absolute steps there, switched on only when it learned
    relative actions."""
    n = len(JOINTS)
    rename, batch, device, normalizer, unnormalizer = up.DEFAULT_STEP_NAMES
    newline, tokenizer, to_relative, to_absolute = up.PI0_STEP_NAMES
    image = {"type": "VISUAL", "shape": [3, 480, 640]}
    config = {
        "type": "pi0",
        "input_features": {
            up.STATE_KEY: {"type": "STATE", "shape": [n]},
            f"{up.IMAGES_PREFIX}top": image,
            f"{up.IMAGES_PREFIX}wrist": image,
        },
        "output_features": {up.ACTION_KEY: {"type": "ACTION", "shape": [n]}},
        "device": "cuda",
        "chunk_size": 50,
        "n_action_steps": 50,
        "use_relative_actions": relative,
        "relative_exclude_joints": ["gripper"],
        "action_feature_names": [f"{m}.pos" for m in JOINTS],
        "tokenizer_max_length": 48,
        "empty_cameras": 0,
    }
    pre = {
        "name": "policy_preprocessor",
        "steps": [
            {"registry_name": rename, "config": {"rename_map": {}}},
            {"registry_name": batch, "config": {}},
            {"registry_name": newline, "config": {}},
            {
                "registry_name": tokenizer,
                "config": {"max_length": 48, "task_key": "task", "tokenizer_name": PALIGEMMA},
            },
            {"registry_name": device, "config": {"device": "cuda", "float_dtype": None}},
            {
                "registry_name": to_relative,
                "config": {
                    "enabled": relative,
                    "exclude_joints": ["gripper"],
                    "action_names": None,
                },
            },
            {
                "registry_name": normalizer,
                "config": {"eps": 1e-8},
                "state_file": f"policy_preprocessor_step_6_{normalizer}.safetensors",
            },
        ],
    }
    post = {
        "name": "policy_postprocessor",
        "steps": [
            {
                "registry_name": unnormalizer,
                "config": {"eps": 1e-8},
                "state_file": f"policy_postprocessor_step_0_{unnormalizer}.safetensors",
            },
            {"registry_name": to_absolute, "config": {"enabled": relative}},
            {"registry_name": device, "config": {"device": "cpu", "float_dtype": None}},
        ],
    }
    return _hub(tmp_path, config=config, pre=pre, post=post)


@pytest.mark.parametrize("relative", [False, True])
def test_a_pi0_is_served_in_chunks_with_its_tokenizer_pinned(
    tmp_path: Path, relative: bool
) -> None:
    """A pi0 is a pi05 with a step that ends its task with a newline in place of pi05's state
    tokenizer (`upstream_api.PI0_PROCESSOR_STEPS`). Its tokenizer is PaliGemma's, pinned and
    fetched without PaliGemma's weights, it pads a camera it is not given, it names its
    action's motors for the arm to check, and one that learned relative actions is served a
    chunk at a time, as a pi05 is."""
    hub = _pi0(tmp_path, relative=relative)
    with pytest.raises(P.PipelineRefused, match=f"--pin {PALIGEMMA}@REVISION"):
        _fetch(hub, fps=float(FPS))
    assert up.WEIGHTS_FILE not in [filename for _, _, filename in hub.asked]
    checkpoint = _fetch(hub, fps=float(FPS), pins=(f"{PALIGEMMA}@{PINNED}",))
    shape = checkpoint.shape
    assert (shape.policy_type, shape.chunk_size, shape.n_action_steps) == ("pi0", 50, 50)
    assert shape.pads_images and not shape.per_tick
    assert shape.action_names == tuple(f"{m}.pos" for m in JOINTS)
    ((repo, revision, patterns),) = hub.snapshots
    assert (repo, revision, patterns) == (PALIGEMMA, PINNED, P.TOKENIZER_FILES)
    where = str(hub.snapshot(repo, revision, patterns))
    assert checkpoint.pre_overrides["tokenizer_processor"] == {"tokenizer_name": where}
    assert f"tokenizer_name {PALIGEMMA}@{PINNED}" in checkpoint.loaded
    assert "pretrained_backbone_weights" not in checkpoint.config_overrides


@pytest.mark.parametrize(
    ("config", "needle"),
    [
        (
            _act_config(type="pi0_fast", action_tokenizer_name="physical-intelligence/fast"),
            "FAST tokenizer's own Python code",
        ),
    ],
)
def test_a_family_quackd_read_and_will_not_serve_is_refused_from_its_config_alone(
    tmp_path: Path, config: dict[str, Any], needle: str
) -> None:
    """A type whose own code runs a repository's Python, whatever a checkpoint of it says, is
    refused on its `config.json`, before its processors are so much as fetched, in a sentence
    that says why and what to serve instead (`REFUSED_TYPES`)."""
    hub = _hub(tmp_path, config=config)
    with pytest.raises(P.PipelineRefused, match=needle) as refused:
        _fetch(hub, fps=float(FPS))
    assert "instead" in str(refused.value)
    assert [filename for _, _, filename in hub.asked] == [up.CONFIG_FILE]


BART = "facebook/bart-large"
"""The tokenizer an X-VLA's processor names unless its config says otherwise."""


def _xvla(
    tmp_path: Path,
    *,
    drop: Sequence[str] = (),
    domain_id: Any = 0,
    post_step: Mapping[str, Any] | None = None,
    **over: Any,
) -> FakeHub:
    """An X-VLA as `mertkirgil/xvla-so101-renkli-kalem` was saved, read on 2026-10-25: `auto`
    mode, six motors, a front camera, chunks of 30, 30 domains with its step handing over the
    first, BART's tokenizer named in its step, Florence-2 described in its config and never
    named as a model, and its actions normalised by their mean and deviation. `drop` leaves
    keys out of its config, and `post_step` adds a step to its post-processor."""
    n = len(JOINTS)
    rename, batch, device, normalizer, unnormalizer = up.DEFAULT_STEP_NAMES
    tokenizer, to_float, imagenet, domain, _ = up.XVLA_STEP_NAMES
    config = {
        "type": "xvla",
        "input_features": {
            up.STATE_KEY: {"type": "STATE", "shape": [n]},
            f"{up.IMAGES_PREFIX}front": {"type": "VISUAL", "shape": [3, 480, 640]},
        },
        "output_features": {up.ACTION_KEY: {"type": "ACTION", "shape": [n]}},
        "device": "cuda",
        "chunk_size": 30,
        "n_action_steps": 30,
        "action_mode": "auto",
        "num_domains": 30,
        "domain_feature_key": None,
        "tokenizer_name": BART,
        "max_action_dim": 20,
        "max_state_dim": 20,
        "num_image_views": 3,
        "use_proprio": True,
        "florence_config": {"vision_config": {"model_type": "davit"}, "text_config": {}},
        "normalization_mapping": {"STATE": "IDENTITY", "ACTION": "MEAN_STD", "VISUAL": "IDENTITY"},
        **over,
    }
    for key in drop:
        del config[key]
    pre = {
        "name": "policy_preprocessor",
        "steps": [
            {"registry_name": rename, "config": {"rename_map": {}}},
            {"registry_name": batch, "config": {}},
            {"registry_name": tokenizer, "config": {"max_length": 50, "tokenizer_name": BART}},
            {"registry_name": domain, "config": {"domain_id": domain_id}},
            {"registry_name": to_float, "config": {"image_keys": None, "validate_range": True}},
            {"registry_name": imagenet, "config": {"image_keys": None}},
            {"registry_name": device, "config": {"device": "cuda", "float_dtype": None}},
            {
                "registry_name": normalizer,
                "config": {"eps": 1e-8},
                "state_file": f"policy_preprocessor_step_7_{normalizer}.safetensors",
            },
        ],
    }
    post = {
        "name": "policy_postprocessor",
        "steps": [
            {
                "registry_name": unnormalizer,
                "config": {"eps": 1e-8},
                "state_file": f"policy_postprocessor_step_0_{unnormalizer}.safetensors",
            },
            *([post_step] if post_step is not None else []),
            {"registry_name": device, "config": {"device": "cpu", "float_dtype": None}},
        ],
    }
    return _hub(tmp_path, config=config, pre=pre, post=post)


def test_an_xvla_in_auto_mode_is_served_with_its_tokenizer_pinned(tmp_path: Path) -> None:
    """An X-VLA in `auto` mode answers in the dataset's own action, an SO-101's joints. Its
    Florence-2 is built from its config, so BART's tokenizer is the one model it names, pinned
    and fetched without weights, and it runs with a camera it is not given padded. LeRobot lowers
    the mode before it reads it, so the server does too (`upstream_api.XVLA_ACTION_MODES`)."""
    hub = _xvla(tmp_path)
    with pytest.raises(P.PipelineRefused, match=f"--pin {BART}@REVISION"):
        _fetch(hub, fps=float(FPS))
    checkpoint = _fetch(hub, fps=float(FPS), pins=(f"{BART}@{PINNED}",))
    shape = checkpoint.shape
    assert (shape.policy_type, shape.chunk_size, shape.n_action_steps) == ("xvla", 30, 30)
    assert shape.pads_images and not shape.per_tick
    ((repo, revision, patterns),) = hub.snapshots
    assert (repo, revision, patterns) == (BART, PINNED, P.TOKENIZER_FILES)
    where = str(hub.snapshot(repo, revision, patterns))
    assert checkpoint.pre_overrides["tokenizer_processor"] == {"tokenizer_name": where}
    assert "pretrained_backbone_weights" not in checkpoint.config_overrides
    upper = _xvla(tmp_path / "upper", action_mode="AUTO")
    assert _fetch(upper, fps=float(FPS), pins=(f"{BART}@{PINNED}",)).shape.policy_type == "xvla"


_ROTATION = {
    "registry_name": "xvla_rotation_6d_to_axis_angle",
    "config": {"expected_action_dim": 10},
}


@pytest.mark.parametrize(
    ("kw", "needle"),
    [
        ({"action_mode": "ee6d"}, "action_mode is 'ee6d', which is not this arm's joints"),
        ({"action_mode": "joint"}, "action_mode is 'joint'"),
        ({"action_mode": "so101_bimanual"}, "action_mode is 'so101_bimanual'"),
        ({"action_mode": "franka_joint7"}, "action_mode is 'franka_joint7'"),
        ({"drop": ["action_mode"]}, "action_mode is 'ee6d'"),
        ({"action_mode": 7}, "action_mode is 7"),
        ({"domain_id": 30}, "domain 30, and it has 30 domains"),
        ({"domain_id": -1}, "hands the model domain -1"),
        ({"domain_id": True}, "hands the model domain True"),
        ({"domain_id": "0"}, "hands the model domain '0'"),
        ({"num_domains": 0}, "not a count of domains"),
        ({"post_step": _ROTATION}, "axis-angle and the gripper into -1 or 1"),
    ],
)
def test_an_xvla_that_would_not_answer_in_this_arms_joints_is_refused_before_its_weights(
    tmp_path: Path, kw: dict[str, Any], needle: str
) -> None:
    """Every mode but `auto` decodes the answer as an end effector's pose or another robot's
    joints, and LIBERO's rotation step turns it into an axis and an angle and a gripper of -1 or
    1, none of which an SO-101 takes. A domain past the model's is a row it does not have. Each
    is refused in a sentence of its own, before the tokenizer's pin or a weight is fetched."""
    hub = _xvla(tmp_path, **kw)
    with pytest.raises(P.PipelineRefused, match=re.escape(needle)):
        _fetch(hub, fps=float(FPS), pins=(f"{BART}@{PINNED}",))
    assert up.WEIGHTS_FILE not in [filename for _, _, filename in hub.asked]
    assert not hub.snapshots


class _Stat:
    """A statistic as a step's `state_dict` holds it, with what `_quantiles` asks a tensor."""

    def __init__(self, values: Sequence[float]) -> None:
        self.values = list(values)

    def detach(self) -> _Stat:
        return self

    def cpu(self) -> _Stat:
        return self

    def reshape(self, *shape: int) -> _Stat:
        return self

    def tolist(self) -> list[float]:
        return list(self.values)


class _Normaliser:
    """A pre-processor whose one step keeps the state's percentiles, as the normaliser does."""

    def __init__(self, q01: Sequence[float], q99: Sequence[float]) -> None:
        stats = {f"{up.STATE_KEY}.q01": _Stat(q01), f"{up.STATE_KEY}.q99": _Stat(q99)}
        step = type("Step", (), {"state_dict": lambda self: stats})()
        self.steps = [step]


def test_percentiles_a_type_keeps_elsewhere_come_before_the_normalisers(tmp_path: Path) -> None:
    """A type whose own steps keep the state's percentiles somewhere the normaliser's are not
    says them in the checkpoint (`Family`), and those are what the runner reports, which the
    arm holds against its travel. Without them the normaliser's are reported, as before."""
    n = len(JOINTS)
    normalised = _Normaliser([-1.0] * n, [1.0] * n)
    empty = _Normaliser([], [])
    empty.steps = []
    checkpoint = _fetch(_hub(tmp_path), fps=float(FPS))

    def runner(checkpoint: P.Checkpoint) -> P.LeRobotRunner:
        return P.LeRobotRunner(
            checkpoint,
            policy=object(),
            preprocessor=normalised,
            postprocessor=empty,
            cameras={},
            threads=1,
            latency_s=0.0,
        )

    assert runner(checkpoint).state_quantiles == wire.Quantiles(q01=[-1.0] * n, q99=[1.0] * n)
    own = wire.Quantiles(q01=[-2.0] * n, q99=[2.0] * n)
    acted = wire.Quantiles(q01=[-3.0] * n, q99=[3.0] * n)
    kept = dataclasses.replace(checkpoint, state_quantiles=own, action_quantiles=acted)
    assert (runner(kept).state_quantiles, runner(kept).action_quantiles) == (own, acted)


def test_a_reset_resets_the_policy_and_both_processors(tmp_path: Path) -> None:
    """In chunks, ACT's `predict_action_chunk` never reads the queue its reset clears, so a
    replay after a reset cannot show the reset happened. It is counted here instead."""

    class Resettable:
        steps: list[Any] = []

        def __init__(self) -> None:
            self.resets = 0

        def reset(self) -> None:
            self.resets += 1

    policy, pre, post = Resettable(), Resettable(), Resettable()
    runner = P.LeRobotRunner(
        _fetch(_hub(tmp_path), fps=float(FPS)),
        policy=policy,
        preprocessor=pre,
        postprocessor=post,
        cameras={},
        threads=1,
        latency_s=0.0,
    )
    runner.reset("reach")
    runner.reset("place")
    assert (policy.resets, pre.resets, post.resets) == (2, 2, 2)


def test_closing_a_checkpoint_waits_on_no_step_still_inferring(tmp_path: Path) -> None:
    """A step holds the runner's lock for as long as torch takes, minutes on a CPU. Closing
    the runner, which is what stopping the server does, never waits for it, and no session
    begins after it, since a begin would give a closed policy back the features it steps on."""

    class Inert:
        steps: list[Any] = []

        def reset(self) -> None:
            pass

    runner = P.LeRobotRunner(
        _fetch(_hub(tmp_path), fps=float(FPS)),
        policy=Inert(),
        preprocessor=Inert(),
        postprocessor=Inert(),
        cameras={},
        threads=1,
        latency_s=0.0,
    )
    runner.begin(JOINTS, {})
    closed = threading.Event()
    with runner._lock:  # a step inferring
        worker = threading.Thread(target=lambda: (runner.close(), closed.set()), daemon=True)
        worker.start()
        assert closed.wait(S.CLOSE_WAIT_S), "the close waited on the step"
    with pytest.raises(RuntimeError, match="closed"):
        runner.begin(JOINTS, {})
    assert runner._features is None, "a begin after the close undid it"


def test_the_rate_is_fps_or_the_training_datasets_at_the_revision_it_names(tmp_path: Path) -> None:
    given = _hub(tmp_path / "given")
    checkpoint = _fetch(given, fps=float(FPS * 3))
    assert (checkpoint.rate_hz, checkpoint.rate_source) == (FPS * 3, "--fps")
    assert up.TRAIN_CONFIG_FILE not in [filename for _, _, filename in given.asked]
    assert checkpoint.loaded == (f"checkpoint {REPO}@{_commit(REPO)}",)
    for name, train, needle in (
        ("absent", None, f"no {up.TRAIN_CONFIG_FILE}"),
        ("unpinned", {"dataset": {"repo_id": DATASET, "revision": None}}, "at no revision"),
        ("several", {"dataset": {"repo_id": [DATASET, DATASET]}}, "not one dataset"),
        (
            "elsewhere",
            {"dataset": {"repo_id": DATASET, "revision": _commit("elsewhere")}},
            "no meta/info.json",
        ),
    ):
        with pytest.raises(P.PipelineRefused, match=needle) as refused:
            _fetch(_hub(tmp_path / name, **{up.TRAIN_CONFIG_FILE: train}))
        assert "--fps" in str(refused.value), name
    # JSON reads an integer of any length, and one this long is more than a float holds
    for n, fps in enumerate((None, True, "10", math.nan, 10**400)):
        hub = _hub(tmp_path / f"fps-{n}")
        hub.files[(DATASET, TAG)][up.DATASET_INFO_FILE] = {"fps": fps} if fps is not None else {}
        if isinstance(fps, float):  # NaN is not JSON: the file says it the way Python writes it
            hub.files[(DATASET, TAG)][up.DATASET_INFO_FILE] = '{"fps": NaN}'
        with pytest.raises(P.PipelineRefused, match="has no fps"):
            _fetch(hub)


def test_a_dataset_or_a_pin_at_a_branch_is_refused(tmp_path: Path) -> None:
    """The revision a rate is read at, which the checkpoint chose, and the one a nested model
    is fetched at are each a commit or a tag, which name the same files from one serve to the
    next. A branch or a pull request's ref names whatever was last pushed to it. A commit is
    taken by its shape, and a tag only when the Hub says it is one, so with the Hub out of
    reach only a commit is."""
    for revision in ("main", "refs/pr/3", _commit(DATASET)[:7]):
        hub = _hub(tmp_path / revision.replace("/", "-"))
        train = {"dataset": {"repo_id": DATASET, "revision": revision}}
        hub.files[(REPO, _commit(REPO))][up.TRAIN_CONFIG_FILE] = train
        hub.files[(DATASET, revision)] = {up.DATASET_INFO_FILE: {"fps": FPS}}
        with pytest.raises(P.PipelineRefused, match="neither a whole commit nor one of its tags"):
            _fetch(hub)
        assert up.DATASET_INFO_FILE not in [filename for _, _, filename in hub.asked]
    at_commit = _hub(tmp_path / "commit")
    train = {"dataset": {"repo_id": DATASET, "revision": _commit(DATASET)}}
    at_commit.files[(REPO, _commit(REPO))][up.TRAIN_CONFIG_FILE] = train
    at_commit.files[(DATASET, _commit(DATASET))] = {up.DATASET_INFO_FILE: {"fps": FPS}}
    at_commit.unreachable = "the Hub is out of reach"
    assert _fetch(at_commit).rate_hz == FPS and not at_commit.tags_asked
    at_tag = _hub(tmp_path / "tag")
    assert _fetch(at_tag).rate_hz == FPS and at_tag.tags_asked == [DATASET]
    at_tag.unreachable = "HF_HUB_OFFLINE is set, so the Hub cannot be asked"
    with pytest.raises(P.PipelineRefused, match="not a whole commit, and HF_HUB_OFFLINE") as out:
        _fetch(at_tag)
    assert "--fps" in str(out.value)

    backbone = "SomeOrg/Some-VLM"
    for revision in ("main", "refs/pr/3", "abc123"):
        hub = _smolvla(tmp_path / f"pin-{revision.replace('/', '-')}")
        with pytest.raises(P.PipelineRefused, match="neither a whole commit nor one of its tags"):
            _fetch(hub, fps=float(FPS), pins=(f"{backbone}@{revision}",))
        assert up.WEIGHTS_FILE not in [filename for _, _, filename in hub.asked]
        assert not hub.snapshots
    hub = _smolvla(tmp_path / "pin-tag")
    hub.tagged[backbone] = frozenset({"v2"})
    pinned = _fetch(hub, fps=float(FPS), pins=(f"{backbone}@v2",))
    assert f"vlm_model_name {backbone}@v2" in pinned.loaded
    hub.unreachable = "HF_HUB_OFFLINE is set, so the Hub cannot be asked"
    with pytest.raises(P.PipelineRefused, match="pin a whole commit"):
        _fetch(hub, fps=float(FPS), pins=(f"{backbone}@v2",))
    _fetch(hub, fps=float(FPS), pins=(f"{backbone}@{PINNED}",))


def test_the_hubs_tags_leave_out_its_branches_and_a_tag_a_branch_shares(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What `HubFetch` takes to be a tag: one the Hub lists as a tag, by its name or its ref,
    and not one whose name a branch has too, since which of the two a fetch would get is not
    quackd's to guess."""
    hub_api = pytest.importorskip("huggingface_hub")
    from huggingface_hub.errors import OfflineModeIsEnabled
    from huggingface_hub.hf_api import GitRefInfo, GitRefs

    def ref(kind: str, name: str) -> GitRefInfo:
        return GitRefInfo(name=name, ref=f"refs/{kind}/{name}", target_commit=_commit(name))

    asked: list[tuple[str, str | None]] = []

    def list_repo_refs(self: Any, repo_id: str, *, repo_type: str | None = None) -> GitRefs:
        asked.append((repo_id, repo_type))
        return GitRefs(
            branches=[ref("heads", "main"), ref("heads", "v2")],
            converts=[ref("convert", "parquet")],
            tags=[ref("tags", TAG), ref("tags", "v2")],
        )

    monkeypatch.setattr(hub_api.HfApi, "list_repo_refs", list_repo_refs)
    assert P.HubFetch().tags(DATASET, dataset=True) == {TAG, f"refs/tags/{TAG}"}
    assert asked == [(DATASET, "dataset")]

    def offline(self: Any, repo_id: str, **kw: Any) -> GitRefs:
        raise OfflineModeIsEnabled("Cannot reach the Hub: offline mode is enabled")

    monkeypatch.setattr(hub_api.HfApi, "list_repo_refs", offline)
    with pytest.raises(P.PipelineRefused, match="HF_HUB_OFFLINE is set"):
        P.HubFetch().tags(REPO)


@pytest.mark.parametrize(
    ("config", "needle"),
    [
        (_act_config(type="diffusion"), "'diffusion' policy"),
        (
            _act_config(
                input_features={
                    up.STATE_KEY: {"type": "STATE", "shape": [len(JOINTS)]},
                    "observation.environment_state": {"type": "ENV", "shape": [3]},
                }
            ),
            "not an image",
        ),
        (
            _act_config(
                input_features={f"{up.IMAGES_PREFIX}front": {"type": "VISUAL", "shape": [3, 4, 4]}}
            ),
            "takes no observation.state",
        ),
        (
            _act_config(
                output_features={
                    up.ACTION_KEY: {"type": "ACTION", "shape": [len(JOINTS)]},
                    "reward": {"type": "REWARD", "shape": [1]},
                }
            ),
            "one action",
        ),
        (_act_config(n_action_steps=CHUNK_SIZE + 1), "n_action_steps"),
        (_act_config(chunk_size=True), "chunk_size"),
        (_act_config(n_action_steps=1, temporal_ensemble_coeff=0.01), "no GPU"),
        (_act_config(action_feature_names=["shoulder pan"]), "action_feature_names"),
        (
            _act_config(
                input_features={
                    up.STATE_KEY: {"type": "STATE", "shape": [len(JOINTS)]},
                    f"{up.IMAGES_PREFIX}front camera": {"type": "VISUAL", "shape": [3, 4, 4]},
                }
            ),
            "not an image key",
        ),
        ([], "not a JSON object"),
    ],
)
def test_what_config_json_says_is_checked(tmp_path: Path, config: Any, needle: str) -> None:
    with pytest.raises(P.PipelineRefused, match=needle):
        _fetch(_hub(tmp_path, config=config), fps=float(FPS))


def test_temporal_ensembling_on_a_gpu_is_a_policy_asked_every_tick(tmp_path: Path) -> None:
    hub = _hub(tmp_path, config=_act_config(n_action_steps=1, temporal_ensemble_coeff=0.01))
    assert _fetch(hub, fps=float(FPS), device="cuda").shape.per_tick


def test_each_camera_is_the_image_of_its_own_name_unless_cameras_says_otherwise() -> None:
    shape = P.check_config(_act_config(), "x/y@z", gpu=False)
    assert P.camera_map({}, shape, "x/y@z") == {"front": f"{up.IMAGES_PREFIX}front"}
    assert P.camera_map({"wrist": f"{up.IMAGES_PREFIX}front"}, shape, "x/y@z") == {
        "wrist": f"{up.IMAGES_PREFIX}front"
    }
    with pytest.raises(P.PipelineRefused, match=r"looks at observation\.images\.front"):
        P.camera_map({"wrist": f"{up.IMAGES_PREFIX}top"}, shape, "x/y@z")


@pytest.mark.parametrize(
    "spec", ["x/y", "x/y@", "\u00f3wner/y@main", "x/y@main extra", f"x/{'y' * 200}@main"]
)
def test_a_repository_is_named_in_ascii_at_a_revision(spec: str) -> None:
    with pytest.raises(P.PipelineRefused, match="REPO@REVISION"):
        P.parse_repo(spec, "--policy")


# ── a tiny random ACT, served for real (CI's torch job) ─────────────────────────────────

TORCHLESS = importlib.util.find_spec("torch") is None or importlib.util.find_spec("lerobot") is None
TRANSFORMERLESS = importlib.util.find_spec("transformers") is None
if (TORCHLESS or TRANSFORMERLESS) and os.environ.get("QUACKD_REQUIRE_TORCH"):
    raise RuntimeError(
        "QUACKD_REQUIRE_TORCH is set and torch, LeRobot or transformers is not installed here, so "
        "the tiny ACT or the tiny X-VLA would be skipped and prove nothing: install them, as CI's "
        "policy job does"
    )
NEEDS_TORCH = pytest.mark.skipif(
    TORCHLESS, reason="torch and LeRobot are CI's torch job's, not the default suite's"
)
NEEDS_TRANSFORMERS = pytest.mark.skipif(
    TORCHLESS or TRANSFORMERLESS,
    reason="an X-VLA's Florence-2 and its tokenizer are transformers', which CI's torch job has",
)


@dataclass(frozen=True)
class TinyAct:
    """A tiny random ACT in a Hub cache of its own: the cache, the checkpoint's commit, and the
    statistics it was normalised with, in the bus order of the arm the tests drive with it."""

    cache: Path
    commit: str
    motors: tuple[str, ...]
    q01: tuple[float, ...]
    q99: tuple[float, ...]

    @property
    def spec(self) -> str:
        return f"{REPO}@{self.commit}"


def _cached(cache: Path, repo: str, kind: str, name: str) -> Path:
    """Where the Hub's cache keeps `repo` at the commit `_commit(name)`, tagged `TAG`."""
    root = cache / f"{kind}--{repo.replace('/', '--')}"
    (root / "refs").mkdir(parents=True, exist_ok=True)
    (root / "refs" / TAG).write_text(_commit(name))
    snapshot = root / "snapshots" / _commit(name)
    snapshot.mkdir(parents=True, exist_ok=True)
    return snapshot


@pytest.fixture(scope="module")
def tiny_act(tmp_path_factory: pytest.TempPathFactory) -> TinyAct:
    """Built once per run: an ACT with a small chunk, a state and an action the size of the
    arm's bus and one front camera the size of the test suite's frames, normalised with
    statistics that sit inside the synthetic arm's travel. It is built with no pretrained
    backbone, since building one fetches ImageNet weights, and saved naming LeRobot's own
    default backbone, as a trained ACT's config does, so that serving it proves the server
    never fetches one (`upstream_api.ACT_BACKBONE_WEIGHTS`, and `offline`)."""
    torch = pytest.importorskip("torch")
    pytest.importorskip("lerobot")

    from lerobot.configs.types import FeatureType, PolicyFeature
    from lerobot.policies.act.configuration_act import ACTConfig
    from lerobot.policies.act.modeling_act import ACTPolicy
    from lerobot.policies.factory import make_pre_post_processors

    arm = _segment_arm()
    motors = tuple(arm.bus.motors)
    travel = joint_ranges(arm.calibration)
    q01 = tuple(
        (travel[m][0] + travel[m][1]) / 2 - (travel[m][1] - travel[m][0]) / 4 for m in motors
    )
    q99 = tuple(
        (travel[m][0] + travel[m][1]) / 2 + (travel[m][1] - travel[m][0]) / 4 for m in motors
    )
    n = len(motors)
    image = f"{up.IMAGES_PREFIX}front"
    config = ACTConfig(
        input_features={
            up.STATE_KEY: PolicyFeature(type=FeatureType.STATE, shape=(n,)),
            image: PolicyFeature(type=FeatureType.VISUAL, shape=(3, FRAME_H, FRAME_W)),
        },
        output_features={up.ACTION_KEY: PolicyFeature(type=FeatureType.ACTION, shape=(n,))},
        chunk_size=CHUNK_SIZE,
        n_action_steps=N_ACTION_STEPS,
        dim_model=32,
        n_heads=2,
        dim_feedforward=64,
        n_encoder_layers=1,
        n_decoder_layers=1,
        n_vae_encoder_layers=1,
        latent_dim=4,
        pretrained_backbone_weights=None,
        device="cpu",
    )
    torch.manual_seed(0)
    policy = ACTPolicy(config)

    def stats(low: Sequence[float], high: Sequence[float]) -> dict[str, Any]:
        lo, hi = torch.tensor(low), torch.tensor(high)
        middle, spread = (lo + hi) / 2, (hi - lo) / 2
        return {"mean": middle, "std": spread, "min": lo, "max": hi, "q01": lo, "q99": hi}

    pixels = {
        k: torch.full((3, 1, 1), v)
        for k, v in (("mean", 0.5), ("std", 0.25), ("min", 0.0), ("max", 1.0))
    }
    pre, post = make_pre_post_processors(
        config,
        dataset_stats={
            up.STATE_KEY: stats(q01, q99),
            up.ACTION_KEY: stats(q01, q99),
            image: pixels,
        },
    )
    cache = tmp_path_factory.mktemp("hub")
    snapshot = _cached(cache, REPO, "models", REPO)
    backbone = {f.name: f.default for f in dataclasses.fields(ACTConfig)}[
        "pretrained_backbone_weights"
    ]
    assert backbone is not None, "LeRobot's ACT no longer names a backbone to fetch"
    policy.config.pretrained_backbone_weights = backbone
    policy.save_pretrained(snapshot)
    pre.save_pretrained(snapshot)
    post.save_pretrained(snapshot)
    # the dataset at its commit: offline, the Hub cannot be asked whether a name is a tag
    (snapshot / up.TRAIN_CONFIG_FILE).write_text(
        json.dumps({"dataset": {"repo_id": DATASET, "revision": _commit(DATASET)}})
    )
    info = _cached(cache, DATASET, "datasets", DATASET) / up.DATASET_INFO_FILE
    info.parent.mkdir(parents=True, exist_ok=True)
    info.write_text(json.dumps({"codebase_version": "v3.0", "fps": FPS, "features": {}}))
    return TinyAct(cache, _commit(REPO), motors, q01, q99)


@pytest.fixture
def offline(tiny_act: TinyAct, monkeypatch: pytest.MonkeyPatch) -> TinyAct:
    """The Hub is the tiny ACT's cache and nothing else: offline, as CI's job runs it, so a
    fetch that is not in the cache fails rather than reaching the network. torchvision's fetch
    of a pretrained backbone fails too, whatever its own cache holds, since the checkpoint's
    config names one and the server is to build it without."""
    import torchvision.models._api
    from huggingface_hub import constants

    def fetched(url: str, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"a backbone's weights were fetched from {url}")

    monkeypatch.setattr(torchvision.models._api, "load_state_dict_from_url", fetched)
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setattr(constants, "HF_HUB_OFFLINE", True)
    monkeypatch.setattr(constants, "HF_HUB_CACHE", str(tiny_act.cache))
    return tiny_act


@dataclass
class Served:
    app: S.PolicyServer
    http: Any

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{int(self.http.server_address[1])}"


@pytest.fixture
def served_act(offline: TinyAct) -> Iterator[Served]:
    """The tiny ACT at its commit, served by the real server on loopback at the dataset's rate."""
    runner, info = S.served_policy(S.ServeOptions(policy=offline.spec))
    app_ = S.PolicyServer(runner, info, TOKEN)
    serving = Served(app_, S.serve(app_, "127.0.0.1", 0))
    try:
        yield serving
    finally:
        serving.http.shutdown()
        serving.http.server_close()
        app_.close()


def _front(value: int = 128) -> np.ndarray:
    return np.full((FRAME_H, FRAME_W, 3), value, dtype=np.uint8)


@NEEDS_TORCH
def test_the_server_says_what_the_tiny_act_is_and_every_repository_it_loaded(
    served_act: Served, offline: TinyAct
) -> None:
    info = RemoteRunner(served_act.url, token=TOKEN, motors=offline.motors).policy()
    assert info.policy == offline.spec
    dataset = f"{DATASET}@{_commit(DATASET)}"
    assert (info.rate_hz, info.rate_source) == (FPS, f"{dataset} {up.DATASET_INFO_FILE}")
    assert (info.chunk_size, info.n_action_steps, info.per_tick) == (
        CHUNK_SIZE,
        N_ACTION_STEPS,
        False,
    )
    assert info.features.state == info.features.action == len(offline.motors)
    ((image,),) = [info.features.images]
    assert (image.key, image.height, image.width) == (f"{up.IMAGES_PREFIX}front", FRAME_H, FRAME_W)
    assert info.cameras == {"front": image.key}
    # a commit is recorded as it was given
    assert info.loaded == [f"checkpoint {offline.spec}", f"dataset {dataset}, its fps"]
    assert info.gpu is False and info.threads is not None and info.threads >= 1
    assert info.state_quantiles is not None
    assert info.state_quantiles.q01 == pytest.approx(offline.q01)
    assert info.state_quantiles.q99 == pytest.approx(offline.q99)
    assert info.action_quantiles is not None


@NEEDS_TORCH
def test_a_step_is_upstreams_own_pipeline_and_a_reset_starts_it_afresh(
    served_act: Served, offline: TinyAct
) -> None:
    """The chunk comes back cut to `n_action_steps`, a goal for every motor by the bus's names,
    each finite. The same observation after a reset answers the same chunk, and each of its
    actions, in order, is what LeRobot's own `select_action` plays from the same files as it
    pops its queue a tick at a time, which puts the post-processor over the whole chunk against
    upstream's one action at a time, row for row."""
    import torch
    from lerobot.configs.policies import PreTrainedConfig
    from lerobot.policies.factory import get_policy_class, make_pre_post_processors
    from lerobot.policies.utils import build_inference_frame, make_robot_action

    camera = wire.CameraInfo(name="front", height=FRAME_H, width=FRAME_W)
    runner = RemoteRunner(served_act.url, token=TOKEN, motors=offline.motors, cameras=[camera])
    reading = {
        f"{m}.pos": (lo + hi) / 2
        for m, lo, hi in zip(offline.motors, offline.q01, offline.q99, strict=True)
    }
    reading["front"] = _front()
    try:
        runner.reset("reach")
        first = runner.next_chunk(Observation(0, reading), {})
        runner.reset("reach")
        again = runner.next_chunk(Observation(0, reading), {})
    finally:
        runner.close()
    assert len(first.actions) == N_ACTION_STEPS
    for action in first.actions:
        assert set(action) == set(offline.motors)
        assert all(math.isfinite(v) for v in action.values())
    for replayed, played in zip(again.actions, first.actions, strict=True):
        assert dict(replayed) == pytest.approx(dict(played))

    where = str(offline.cache / f"models--{REPO.replace('/', '--')}" / "snapshots" / offline.commit)
    config = PreTrainedConfig.from_pretrained(where)
    config.device, config.pretrained_backbone_weights = "cpu", None
    policy = get_policy_class(config.type).from_pretrained(where, config=config)
    pre, post = make_pre_post_processors(config, pretrained_path=where)
    names = [f"{m}.pos" for m in offline.motors]
    features = {
        up.STATE_KEY: {"dtype": "float32", "shape": (len(names),), "names": names},
        up.ACTION_KEY: {"dtype": "float32", "shape": (len(names),), "names": names},
        f"{up.IMAGES_PREFIX}front": {
            "dtype": "video",
            "shape": (FRAME_H, FRAME_W, 3),
            "names": ["height", "width", "channels"],
        },
    }
    raw = {**{n: reading[n] for n in names}, "front": _front()}
    policy.reset()
    with torch.inference_mode():
        batch = pre(build_inference_frame(raw, torch.device("cpu"), features, task="reach"))
        upstream = [
            make_robot_action(post(policy.select_action(batch)), features)
            for _ in range(N_ACTION_STEPS)
        ]
    for i, (theirs, ours) in enumerate(zip(upstream, first.actions, strict=True)):
        assert {k.removesuffix(".pos"): v for k, v in theirs.items()} == pytest.approx(
            dict(ours), abs=1e-4
        ), f"row {i}"
    assert first.actions[0] != first.actions[-1], "a random ACT's rows differ, so order shows"


@NEEDS_TORCH
async def test_an_arm_connects_to_the_tiny_act_and_a_segment_runs_through_it(
    served_act: Served, offline: TinyAct
) -> None:
    """The arm's check at connect reads the tiny ACT's features and quantiles and lets it on,
    and `manipulate` plays its chunks on the simulator's lockstep clock, inference frozen in
    the loop's turn, through HTTP. A random policy's goals mean nothing, and whatever ended the
    segment, it ran: it was not refused and nothing raised."""
    runner = RemoteRunner(served_act.url, token=TOKEN, motors=offline.motors)
    arm = _segment_arm()
    transport = LeRobotReal(
        "COM5",
        robot=arm,
        policy=runner,
        clock=LockstepClock(),
        max_step_deg=STEP,
        camera=parse_camera_url("opencv://0?name=front"),
        camera_object=FakeCamera(),
    )
    adapter = LeRobotAdapter(transport)
    manifest = await adapter.connect()
    try:
        notes = " ".join(transport.connect_notes)
        assert f"checkpoint {offline.spec}" in notes and "not checked" not in notes, notes
        ex = Executor(registry_from_manifest(manifest, adapter), adapter, confirm=allow_all)
        ran = await ex.run_verb("manipulate", {"instruction": "reach"})
        assert ran.data["ended"] not in ("error", "refused"), ran.summary
        assert ran.data["chunks"] >= 1, ran.summary
        assert ran.ok or ran.data["ended"] not in RAN, ran.summary
        assert arm.actions, "the tiny ACT's goals reached the arm"
    finally:
        await adapter.close()


@NEEDS_TORCH
def test_check_benches_the_tiny_act_and_says_the_latency_to_declare(offline: TinyAct) -> None:
    result = CliRunner().invoke(
        app, ["policy", "check", "--policy", offline.spec, "--bench", "--seconds", "1"]
    )
    assert result.exit_code == 0, result.output
    out = " ".join(result.output.split())
    for needle in (
        f"checkpoint {offline.spec}",
        "steps timed, from the request to its chunk back",
        "--latency-s",
        "achieved",
    ):
        assert needle in out, needle


@NEEDS_TORCH
def test_a_revision_the_cache_does_not_have_is_refused_offline(offline: TinyAct) -> None:
    with pytest.raises(S.ServeRefused, match="not in this machine's Hub cache"):
        S.served_policy(S.ServeOptions(policy=f"{REPO}@{_commit('another')}", fps=float(FPS)))
    # and the tag the cache holds reaches the same commit
    runner, info = S.served_policy(S.ServeOptions(policy=f"{REPO}@{TAG}", fps=float(FPS)))
    runner.close()
    assert info.loaded == [f"checkpoint {REPO}@{TAG}, commit {offline.commit[:12]}"]
    assert info.rate_source == "--fps"


@NEEDS_TORCH
def test_a_dataset_named_at_a_tag_is_refused_offline_without_fps(offline: TinyAct) -> None:
    """The cache keeps a tag and a branch alike, as a name for a commit, so offline only the Hub
    could say which the training run's dataset was named at, and it cannot be asked."""
    import shutil

    snapshots = offline.cache / f"models--{REPO.replace('/', '--')}" / "snapshots"
    tagged = snapshots / _commit("dataset at a tag")
    if not tagged.exists():
        shutil.copytree(snapshots / offline.commit, tagged)
    train = {"dataset": {"repo_id": DATASET, "revision": TAG}}
    (tagged / up.TRAIN_CONFIG_FILE).write_text(json.dumps(train))
    spec = f"{REPO}@{_commit('dataset at a tag')}"
    with pytest.raises(S.ServeRefused, match="HF_HUB_OFFLINE is set") as refused:
        S.served_policy(S.ServeOptions(policy=spec))
    assert "--fps" in str(refused.value)
    runner, info = S.served_policy(S.ServeOptions(policy=spec, fps=float(FPS)))
    runner.close()
    assert info.rate_source == "--fps"


@NEEDS_TORCH
def test_a_checkpoint_whose_weights_are_not_its_models_is_refused(offline: TinyAct) -> None:
    """LeRobot's loader, left lenient, only logs the weights a file lacks and keeps the random
    ones the model was built with. The tiny ACT's files at another commit, with a
    `model.safetensors` that holds nothing of the model, would serve that random network with
    the checkpoint's own processors, and the arm's check at connect would pass it."""
    import shutil

    import torch
    from safetensors.torch import save_file

    snapshots = offline.cache / f"models--{REPO.replace('/', '--')}" / "snapshots"
    broken = snapshots / _commit("broken")
    if not broken.exists():
        shutil.copytree(snapshots / offline.commit, broken)
    save_file({"unrelated": torch.zeros(1)}, str(broken / up.WEIGHTS_FILE))
    with pytest.raises(S.ServeRefused, match="did not load") as refused:
        S.served_policy(S.ServeOptions(policy=f"{REPO}@{_commit('broken')}", fps=float(FPS)))
    assert "unrelated" in str(refused.value) or "Missing" in str(refused.value), refused.value


@NEEDS_TORCH
@pytest.mark.parametrize("lenient", ["pi05", "pi0"])
def test_a_pi05_or_a_pi0_is_loaded_strictly_where_its_own_loader_would_not_be(
    tmp_path: Path, lenient: str
) -> None:
    """pi05's `from_pretrained` catches a load that fails and hands back the model as it was
    built, and pi0's is the same loader (`upstream_api.PI0_FROM_PRETRAINED`). A stand-in with a
    loader like theirs, and a key fix and a `model.` prefix like their own: `build_policy` loads
    its weights itself, every one of them, or raises."""
    from types import SimpleNamespace

    import torch
    from safetensors.torch import save_file

    class LenientPolicy(torch.nn.Module):
        def __init__(self, config: Any) -> None:
            super().__init__()
            self.config = config
            self.model = torch.nn.Linear(2, 2)

        @classmethod
        def from_pretrained(cls, where: str, **kw: Any) -> LenientPolicy:
            return cls(kw["config"])  # what pi05's does when its weights do not load

        def _fix_pytorch_state_dict_keys(self, state: Any, config: Any) -> dict[str, Any]:
            return {k.removeprefix("old_"): v for k, v in state.items()}

    assert lenient in P.LENIENT_LOADERS
    config = SimpleNamespace(type=lenient, device="cpu")
    weight, bias = torch.full((2, 2), 3.0), torch.full((2,), 5.0)
    save_file({"old_weight": weight, "model.bias": bias}, str(tmp_path / up.WEIGHTS_FILE))
    policy = P.build_policy(LenientPolicy, tmp_path, config)
    assert torch.equal(policy.model.weight, weight) and torch.equal(policy.model.bias, bias)
    assert not policy.training
    save_file({"old_weight": weight}, str(tmp_path / up.WEIGHTS_FILE))
    with pytest.raises(RuntimeError, match="bias"):
        P.build_policy(LenientPolicy, tmp_path, config)


@NEEDS_TORCH
def test_torch_gets_as_many_threads_however_often_a_checkpoint_loads(offline: TinyAct) -> None:
    """One fewer than torch takes on its own, asked once: asked again after a load has set it,
    torch says what that load set, and each load would take a thread fewer than the last."""
    import torch

    before = torch.get_num_threads()
    try:
        torch.set_num_threads(before + 2)  # more than any load before this one left it
        counts = []
        for _ in range(2):
            runner, info = S.served_policy(S.ServeOptions(policy=offline.spec, fps=float(FPS)))
            runner.close()
            counts.append(info.threads)
        assert counts[0] == counts[1] == max(1, P.torch_threads() - 1), counts
    finally:
        torch.set_num_threads(before)


# ── a tiny random X-VLA, served for real (CI's torch job, with transformers) ────────────

TINY_TOKENIZER = "quackd-test/tiny-tokenizer"
TINY_XVLA = "quackd-test/tiny-xvla"


@dataclass(frozen=True)
class TinyXvla:
    """A tiny random X-VLA and the tokenizer it names, in a Hub cache of their own, each at a
    commit, and the percentiles its state was normalised with, in `JOINTS`' order."""

    cache: Path
    q01: tuple[float, ...]
    q99: tuple[float, ...]

    @property
    def spec(self) -> str:
        return f"{TINY_XVLA}@{_commit(TINY_XVLA)}"

    @property
    def pin(self) -> str:
        return f"{TINY_TOKENIZER}@{_commit(TINY_TOKENIZER)}"


@pytest.fixture(scope="module")
def tiny_xvla(tmp_path_factory: pytest.TempPathFactory) -> TinyXvla:
    """Built once per run, fetching nothing: an X-VLA in `auto` mode whose Florence-2 is a layer
    or two deep, described in its config as a checkpoint's is, with chunks of `CHUNK_SIZE` of
    which `N_ACTION_STEPS` are played, a state and an action the size of the arm's bus, a front
    camera the size of the suite's frames, and its actions normalised by their mean and
    deviation as the SO-101 fine-tunes' are. Its tokenizer is a word-level one of a few words,
    saved where a pinned repository lands in the Hub's cache, and its processor names it by that
    repository, as a pushed checkpoint names BART's."""
    torch = pytest.importorskip("torch")
    pytest.importorskip("transformers")
    from lerobot.configs.types import FeatureType, NormalizationMode, PolicyFeature
    from lerobot.policies.factory import make_pre_post_processors
    from lerobot.policies.xvla.configuration_xvla import XVLAConfig
    from lerobot.policies.xvla.modeling_xvla import XVLAPolicy
    from tokenizers import Tokenizer, models, pre_tokenizers
    from transformers import PreTrainedTokenizerFast

    cache = tmp_path_factory.mktemp("xvla-hub")
    words = ["[PAD]", "[UNK]", "reach", "the", "cube", "place", "it"]
    vocabulary = Tokenizer(models.WordLevel({w: i for i, w in enumerate(words)}, unk_token="[UNK]"))
    vocabulary.pre_tokenizer = pre_tokenizers.Whitespace()
    tokenizer = _cached(cache, TINY_TOKENIZER, "models", TINY_TOKENIZER)
    PreTrainedTokenizerFast(
        tokenizer_object=vocabulary, unk_token="[UNK]", pad_token="[PAD]"
    ).save_pretrained(tokenizer)
    n = len(JOINTS)
    florence = {
        "vision_config": {
            "model_type": "davit",
            "depths": [1, 1],
            "dim_embed": [16, 32],
            "num_heads": [1, 2],
            "num_groups": [1, 2],
            "patch_size": [7, 3],
            "patch_stride": [4, 2],
            "patch_padding": [3, 1],
            "patch_prenorm": [False, True],
            "window_size": 4,
            "projection_dim": 32,
            "image_pos_embed": {"type": "learned_abs_2d", "max_pos_embeddings": 50},
            "visual_temporal_embedding": {"type": "COSINE", "max_temporal_embeddings": 4},
            "image_feature_source": ["spatial_avg_pool", "temporal_avg_pool"],
        },
        "text_config": {
            "vocab_size": 64,
            "d_model": 32,
            "encoder_layers": 1,
            "decoder_layers": 1,
            "encoder_attention_heads": 2,
            "decoder_attention_heads": 2,
            "encoder_ffn_dim": 64,
            "decoder_ffn_dim": 64,
            "max_position_embeddings": 128,
        },
        "projection_dim": 32,
        "vocab_size": 64,
    }
    config = XVLAConfig(
        input_features={
            up.STATE_KEY: PolicyFeature(type=FeatureType.STATE, shape=(n,)),
            f"{up.IMAGES_PREFIX}front": PolicyFeature(
                type=FeatureType.VISUAL, shape=(3, FRAME_H, FRAME_W)
            ),
        },
        output_features={up.ACTION_KEY: PolicyFeature(type=FeatureType.ACTION, shape=(n,))},
        normalization_mapping={
            "VISUAL": NormalizationMode.IDENTITY,
            "STATE": NormalizationMode.IDENTITY,
            "ACTION": NormalizationMode.MEAN_STD,
        },
        florence_config=florence,
        action_mode=up.XVLA_SERVED_MODE,
        chunk_size=CHUNK_SIZE,
        n_action_steps=N_ACTION_STEPS,
        hidden_size=32,
        depth=1,
        num_heads=2,
        num_domains=2,
        len_soft_prompts=2,
        max_len_seq=128,
        max_action_dim=8,
        max_state_dim=8,
        num_denoising_steps=2,
        tokenizer_name=str(tokenizer),
        tokenizer_max_length=8,
        device="cpu",
    )
    torch.manual_seed(0)
    policy = XVLAPolicy(config)
    q01, q99 = tuple(-30.0 - i for i in range(n)), tuple(30.0 + i for i in range(n))
    lo, hi = torch.tensor(q01), torch.tensor(q99)
    stats = {
        "mean": (lo + hi) / 2,
        "std": (hi - lo) / 2,
        "min": lo,
        "max": hi,
        "q01": lo,
        "q99": hi,
    }
    pre, post = make_pre_post_processors(
        config, dataset_stats={up.STATE_KEY: stats, up.ACTION_KEY: dict(stats)}
    )
    snapshot = _cached(cache, TINY_XVLA, "models", TINY_XVLA)
    policy.save_pretrained(snapshot)
    pre.save_pretrained(snapshot)
    post.save_pretrained(snapshot)
    # a pushed checkpoint names its tokenizer by its Hub id, and the pin says at which commit
    for name in (up.CONFIG_FILE, up.PREPROCESSOR_FILE):
        saved = json.loads((snapshot / name).read_text(encoding="utf-8"))
        if "tokenizer_name" in saved:
            saved["tokenizer_name"] = TINY_TOKENIZER
        for step in saved.get("steps", []):
            if "tokenizer_name" in step.get("config", {}):
                step["config"]["tokenizer_name"] = TINY_TOKENIZER
        (snapshot / name).write_text(json.dumps(saved), encoding="utf-8")
    return TinyXvla(cache, q01, q99)


@pytest.fixture
def offline_xvla(tiny_xvla: TinyXvla, monkeypatch: pytest.MonkeyPatch) -> TinyXvla:
    """The Hub is the tiny X-VLA's cache and nothing else, offline, as CI's job runs it."""
    from huggingface_hub import constants

    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setattr(constants, "HF_HUB_OFFLINE", True)
    monkeypatch.setattr(constants, "HF_HUB_CACHE", str(tiny_xvla.cache))
    return tiny_xvla


@NEEDS_TRANSFORMERS
def test_a_tiny_xvla_is_served_offline_with_its_tokenizer_pinned(offline_xvla: TinyXvla) -> None:
    """The second family CI's job serves through the real server and client: its config,
    processors and pin read and checked as every checkpoint's are, its tokenizer fetched from
    the cache alone and with no weight, its Florence-2 built from its config, its weights loaded
    by X-VLA's own strict loader, and a chunk back with a finite goal for every motor. A random
    policy says nothing about what a trained one does to an arm."""
    runner, info = S.served_policy(
        S.ServeOptions(policy=offline_xvla.spec, fps=float(FPS), pins=(offline_xvla.pin,))
    )
    app_ = S.PolicyServer(runner, info, TOKEN)
    http = S.serve(app_, "127.0.0.1", 0)
    camera = wire.CameraInfo(name="front", height=FRAME_H, width=FRAME_W)
    client = RemoteRunner(
        f"http://127.0.0.1:{int(http.server_address[1])}",
        token=TOKEN,
        motors=JOINTS,
        cameras=[camera],
    )
    try:
        said = client.policy()
        loaded = [f"checkpoint {offline_xvla.spec}", f"tokenizer_name {offline_xvla.pin}"]
        assert said.loaded == loaded
        assert said.features.pads_images and said.cameras == {"front": f"{up.IMAGES_PREFIX}front"}
        assert (said.chunk_size, said.n_action_steps) == (CHUNK_SIZE, N_ACTION_STEPS)
        assert said.state_quantiles is not None
        assert said.state_quantiles.q01 == pytest.approx(offline_xvla.q01)
        assert said.state_quantiles.q99 == pytest.approx(offline_xvla.q99)
        reading: dict[str, Any] = {f"{m}.pos": 0.0 for m in JOINTS}
        reading["front"] = _front()
        client.reset("reach the cube")
        chunk = client.next_chunk(Observation(0, reading), {})
    finally:
        client.close()
        http.shutdown()
        http.server_close()
        app_.close()
    assert len(chunk.actions) == N_ACTION_STEPS
    for action in chunk.actions:
        assert set(action) == set(JOINTS)
        assert all(math.isfinite(v) for v in action.values())
