"""The only file in quackd allowed to spell a LeRobot policy name (ADR-0022).

Every constant is tagged VERIFIED (read from upstream source at the pin, link given) or
UNVERIFIED (an assumption of ours, with what quackd does about it).
`docs/adapters/lerobot/README.md` is the human-readable version; `tests/test_upstream_api.py` proves
UNVERIFIED names are only reachable from the files that live with them.

These are read against lerobot 0.6.1, the version the laptop that drives the lab arm runs, at
the commit its `v0.6.1` tag names (7e241bd630a3719a56157a497ce5d08f244784f1), and not at the
`main` commit the arm's own refs are pinned to (`quackd_lerobot.upstream_api.PIN`). A policy
runs where a checkpoint was trained and exported, and its processors and its config are read
by whatever lerobot the policy server has installed, so the version people run is the one to
read. The installed 0.6.1 wheel and the tag were compared file by file on the day they were
read, and every file these rows cite was the same.

The policy refs used to live in the arm's own file. They moved here when quackd grew a policy
server (`server.py`), because the server is where a checkpoint is loaded (`pipeline.py`), and
the arm's process never imports torch or LeRobot's policies at all: `client.py` reaches the
server over HTTP. POLICY_PIPELINE is the one row a job exercises: CI's torch job runs a tiny
random ACT and a tiny random X-VLA through the real server (`tests/test_policy_pipeline.py`).
No trained checkpoint has been loaded, no trained X-VLA has run, and SmolVLA, pi05, pi0, GR00T
and tick mode have not run at all: VLA_PIPELINE and TICK_MODE say what quackd does about that.
"""

from __future__ import annotations

from quackd.upstream import UpstreamRef

REPO = "https://github.com/huggingface/lerobot"
PIN = "7e241bd630a3719a56157a497ce5d08f244784f1"  # the v0.6.1 tag
VERSION = "0.6.1"
READ_ON = "2026-09-27"


def src(path: str, line: int | None = None) -> str:
    return f"{REPO}/blob/{PIN}/{path}" + (f"#L{line}" if line else "")


_POLICY = "src/lerobot/policies/pretrained.py"
_FACTORY = "src/lerobot/policies/factory.py"
_POLICY_CFG = "src/lerobot/configs/policies.py"
_UTILS = "src/lerobot/policies/utils.py"
_ACT_CFG = "src/lerobot/policies/act/configuration_act.py"
_SMOLVLA_CFG = "src/lerobot/policies/smolvla/configuration_smolvla.py"
_PIPELINE = "src/lerobot/processor/pipeline.py"
_TOKENIZER = "src/lerobot/processor/tokenizer_processor.py"
_ASYNC = "src/lerobot/async_inference"
_CONSTANTS = "src/lerobot/utils/constants.py"
_TRAIN_CFG = "src/lerobot/configs/train.py"
_DEFAULT_CFG = "src/lerobot/configs/default.py"
_DATASET_UTILS = "src/lerobot/datasets/utils.py"
_PROCESSOR = "src/lerobot/processor"
_NORMALIZE = "src/lerobot/processor/normalize_processor.py"
_DEVICE = "src/lerobot/processor/device_processor.py"
_DEVICE_UTILS = "src/lerobot/utils/device_utils.py"
_PI05_CFG = "src/lerobot/policies/pi05/configuration_pi05.py"
_SMOLVLA = "src/lerobot/policies/smolvla"
_PI05 = "src/lerobot/policies/pi05"
_PI0 = "src/lerobot/policies/pi0"
_PI0_FAST = "src/lerobot/policies/pi0_fast"
_XVLA = "src/lerobot/policies/xvla"
_GROOT = "src/lerobot/policies/groot"
_GROOT_CFG = f"{_GROOT}/configuration_groot.py"
_GROOT_PROC = f"{_GROOT}/processor_groot.py"
_GROOT_N17 = f"{_GROOT}/groot_n1_7.py"
_ACT_MODEL = "src/lerobot/policies/act/modeling_act.py"
_RELATIVE = "src/lerobot/processor/relative_action_processor.py"
_SMOLVLM = "src/lerobot/policies/smolvla/smolvlm_with_expert.py"
_SYNC = "src/lerobot/rollout/inference/sync.py"

# ── a policy (moved here from the arm's own file, and read again at 0.6.1) ──────────────

POLICY_BASE = UpstreamRef(
    "lerobot.policies.pretrained.PreTrainedPolicy", "VERIFIED", src(_POLICY, 105)
)
PRETRAINED_CONFIG = UpstreamRef(
    "lerobot.configs.policies.PreTrainedConfig",
    "VERIFIED",
    src(_POLICY_CFG, 41),
    "`class PreTrainedConfig(draccus.ChoiceRegistry, HubMixin, abc.ABC)`, with "
    "`from_pretrained(pretrained_name_or_path, *, ...)` (line 172), a `device` field (line 62) "
    "and a `type` property (line 99). A checkpoint's config is read through it before the "
    "policy class is built, so it is the one policy name that is not in the factory",
)
CONFIG_HAS_NO_RATE = UpstreamRef(
    "a policy's config carries no fps",
    "VERIFIED",
    src(_POLICY_CFG, 41),
    "nothing in PreTrainedConfig or the policy configs that extend it says how many actions a "
    "second the policy was trained to run at: that is the fps of the dataset it learned from. "
    "So the policy server takes a rate it is given or reads one from where it names, and never "
    "guesses one, and the rate travels to the arm with the source it came from",
)
POLICY_FROM_PRETRAINED = UpstreamRef(
    "PreTrainedPolicy.from_pretrained(path, *, config=None, local_files_only=False, "
    "revision=None, strict=False)",
    "VERIFIED",
    src(_POLICY, 169),
    "a local directory or a Hub repo id, at a revision when one is given; the policy comes back "
    "in eval mode (line 226). Left lenient, a weight the file lacks or one it has that the model "
    "does not is only logged (`_load_as_safetensor`, line 230), and the model keeps what it was "
    "built with there, so the policy server asks for strict=True and refuses a checkpoint whose "
    "weights are not its model's",
)
POLICY_SELECT_ACTION = UpstreamRef(
    "PreTrainedPolicy.select_action(batch: dict[str, Tensor]) -> Tensor",
    "VERIFIED",
    src(_POLICY, 280),
    "one action per call, the policy handles its own action-chunk cache",
)
POLICY_PREDICT_ACTION_CHUNK = UpstreamRef(
    "PreTrainedPolicy.predict_action_chunk(batch: dict[str, Tensor]) -> Tensor",
    "VERIFIED",
    src(_POLICY, 271),
    "the whole chunk for one observation, which is what the policy server answers a step with, "
    "one action per tick from the tick the observation was read at",
)
POLICY_RESET = UpstreamRef("PreTrainedPolicy.reset()", "VERIFIED", src(_POLICY, 245))
GET_POLICY_CLASS = UpstreamRef(
    "lerobot.policies.factory.get_policy_class(name)", "VERIFIED", src(_FACTORY, 79)
)
MAKE_PRE_POST_PROCESSORS = UpstreamRef(
    "lerobot.policies.factory.make_pre_post_processors(policy_cfg, pretrained_path=None, "
    "pretrained_revision=None)",
    "VERIFIED",
    src(_FACTORY, 150),
    "a raw observation goes through the pre-processor and the action tensor through the "
    "post-processor before it is a RobotAction, and both load from the checkpoint at the "
    "revision given",
)
MAKE_POLICY = UpstreamRef(
    "lerobot.policies.factory.make_policy(cfg)", "VERIFIED", src(_FACTORY, 240)
)
BUILD_INFERENCE_FRAME = UpstreamRef(
    "lerobot.policies.utils.build_inference_frame(observation, device, ds_features, task, "
    "robot_type)",
    "VERIFIED",
    src(_UTILS, 141),
    "picks the keys `ds_features` names out of a raw observation and makes them tensors on the "
    "device, which is why the server builds `ds_features` from the motor names and the cameras "
    "a reset declares rather than from the checkpoint alone",
)
MAKE_ROBOT_ACTION = UpstreamRef(
    "lerobot.policies.utils.make_robot_action(action_tensor, ds_features)",
    "VERIFIED",
    src(_UTILS, 175),
    "one action row to a dict named by `ds_features`' action names, with a batch dimension "
    "squeezed off, so a chunk is turned into actions a row at a time",
)

# ── chunks ──────────────────────────────────────────────────────────────────────────────

CHUNK_SIZE = UpstreamRef(
    "ACTConfig.chunk_size and n_action_steps",
    "VERIFIED",
    src(_ACT_CFG, 85),
    "`chunk_size` is how many actions one inference predicts and `n_action_steps` how many of "
    "them are played before the next (line 86), both counted in environment steps, and "
    "`n_action_steps` may not exceed `chunk_size` (line 143). SmolVLA's defaults are 50 and 50 "
    f"({src(_SMOLVLA_CFG, 29)}), and so are pi0's ({src(f'{_PI0}/configuration_pi0.py', 36)}). "
    f"X-VLA's are 32 and 32 ({src(f'{_XVLA}/configuration_xvla.py', 89)}), and GR00T's 40 and "
    f"40 ({src(_GROOT_CFG, 248)}). The policy server reports both",
)
TEMPORAL_ENSEMBLE = UpstreamRef(
    "ACTConfig.temporal_ensemble_coeff",
    "VERIFIED",
    src(_ACT_CFG, 119),
    "None by default. Set, ACT is asked every step and `n_action_steps` must be 1 (line 138), "
    "which is a policy asked every tick: the loop's tick mode",
)

# ── a checkpoint is code (why no quackd command loads one beside the arm's bus) ─────────

PROCESSOR_CLASS_IMPORT = UpstreamRef(
    "a processor step named by class is imported by its module path",
    "VERIFIED",
    src(_PIPELINE, 1085),
    "PolicyProcessorPipeline.from_pretrained resolves each step of a processor's JSON by its "
    "`registry_name`, or else imports whatever `module.Class` its `class` key names with "
    "importlib, so loading a checkpoint's processors can run any code the checkpoint points "
    "at. This is why no quackd command loads a checkpoint in the process that owns the serial "
    "bus. Only `load_policy()` would (LOAD_POLICY), and nothing in quackd calls it",
)
TOKENIZER_TRUSTS_REMOTE_CODE = UpstreamRef(
    "ActionTokenizerProcessorStep.trust_remote_code defaults to True",
    "VERIFIED",
    src(_TOKENIZER, 348),
    "the action tokenizer step (`action_tokenizer_processor`, line 327) loads its tokenizer "
    "trusting the repository's own code unless told not to. The observation tokenizer "
    "SmolVLA, pi05, pi0 and X-VLA use (`tokenizer_processor`, line 55) loads one by "
    "`tokenizer_name` at no "
    "revision (line 111), and SmolVLA names its backbone by an unpinned Hub name "
    f"(`vlm_model_name`, {src(_SMOLVLA_CFG, 84)}). The policy server allows no action "
    "tokenizer, forces trust_remote_code False on any step that has it, and pins every such "
    "name at a commit or a tag or refuses it (`pipeline.py`). A SmolVLA config that leaves "
    "`vlm_model_name` out gets that default filled in, so it is refused. SmolVLA's build asks "
    "transformers for its backbone without saying trust_remote_code either way "
    f"({src(_SMOLVLM, 92)}, to line 101), so a pinned model whose files map a class to code "
    "(`auto_map`), or whose directory in the Hub's cache holds anything but configs, tokenizer "
    "files and safetensors, is refused",
)

# ── loading a checkpoint, and running a step through it (`pipeline.py`) ─────────────────

CHECKPOINT_FILES = UpstreamRef(
    "config.json, model.safetensors, policy_preprocessor.json, policy_postprocessor.json, "
    "train_config.json",
    "VERIFIED",
    src(_CONSTANTS, 58),
    "the files a checkpoint is read from: the config (`CONFIG_NAME`, "
    f"{src(_POLICY_CFG, 188)}), the weights (`SAFETENSORS_SINGLE_FILE`, {src(_POLICY, 204)}), "
    "the two processors under their default names (lines 58 and 59), and the training run's "
    f"config (`TRAIN_CONFIG_NAME`, {src(_TRAIN_CFG, 39)}). The policy server fetches each at "
    "the revision named, the config and the processors before anything else, and nothing "
    "else of the repository",
)
CONFIG_FILE = "config.json"
WEIGHTS_FILE = "model.safetensors"
PREPROCESSOR_FILE = "policy_preprocessor.json"
POSTPROCESSOR_FILE = "policy_postprocessor.json"
TRAIN_CONFIG_FILE = "train_config.json"
"""The names CHECKPOINT_FILES reads, in its order."""
TRAIN_DATASET = UpstreamRef(
    "train_config.json's dataset.repo_id and dataset.revision; meta/info.json's fps",
    "VERIFIED",
    src(_DEFAULT_CFG, 31),
    f"the dataset a policy was trained on (`dataset: DatasetConfig`, {src(_TRAIN_CFG, 80)}), "
    "by its Hub id and a revision that may be None (line 40), and the rate that dataset was "
    f"recorded at, the `fps` of its `meta/info.json` (`INFO_PATH`, {src(_DATASET_UTILS, 92)}, "
    "and `DatasetInfo.fps`, line 125). No config carries a rate (CONFIG_HAS_NO_RATE), so this "
    "is where the policy server reads one when it is given no --fps, and only at a commit or a "
    "tag: the checkpoint chose that revision, and a branch would give a rate that could change "
    "between two serves",
)
DATASET_INFO_FILE = "meta/info.json"
FEATURE_KEYS = UpstreamRef(
    "observation.state, observation.images.<name>, action",
    "VERIFIED",
    src(_CONSTANTS, 23),
    "the keys a policy's state, its images and its action go by (`OBS_STATE`, `OBS_IMAGES` "
    "line 25, `ACTION` line 33). build_inference_frame takes an image's frame from the raw "
    "observation under its key with `observation.images.` cut off "
    f"({src('src/lerobot/utils/feature_utils.py', 134)}), so the server hands it each camera's "
    "frame under that name",
)
STATE_KEY = "observation.state"
IMAGES_PREFIX = "observation.images."
ACTION_KEY = "action"
FEATURE_TYPES = UpstreamRef(
    'FeatureType: "STATE", "VISUAL", "ENV", "ACTION", "REWARD", "LANGUAGE"',
    "VERIFIED",
    src("src/lerobot/configs/types.py", 20),
    "how config.json types each input and output feature. The server serves a checkpoint whose "
    "inputs are one STATE and VISUAL images and whose output is one ACTION, and refuses any "
    "other, since an arm has nothing to give an ENV or a LANGUAGE feature from",
)
POLICY_TYPES = UpstreamRef(
    '"act", "smolvla", "pi05", "pi0", "xvla", "groot"',
    "VERIFIED",
    src(_ACT_CFG, 22),
    "the `type` config.json names its policy by, as ACTConfig, SmolVLAConfig "
    f"({src(_SMOLVLA_CFG, 24)}), PI05Config ({src(_PI05_CFG, 28)}), PI0Config "
    f"({src(f'{_PI0}/configuration_pi0.py', 28)}), XVLAConfig "
    f"({src(f'{_XVLA}/configuration_xvla.py', 76)}) and GrootConfig ({src(_GROOT_CFG, 241)}) "
    "register it. These are the types whose processors the policy server's allowlist was read "
    "from, each its own, and any other type is refused before a byte of its weights is fetched",
)
SERVED_TYPES = ("act", "smolvla", "pi05", "pi0", "xvla", "groot")
DEFAULT_PROCESSOR_STEPS = UpstreamRef(
    "rename_observations_processor, to_batch_processor, device_processor, normalizer_processor, "
    "unnormalizer_processor",
    "VERIFIED",
    src(f"{_PROCESSOR}/factory.py", 100),
    "the steps every policy's processors are built from (make_default_policy_processor_steps), "
    "and all of ACT's (line 158), under the names they register "
    f"({src(f'{_PROCESSOR}/rename_processor.py', 26)}, "
    f"{src(f'{_PROCESSOR}/batch_processor.py', 215)}, {src(_DEVICE, 34)}, "
    f"{src(_NORMALIZE, 425)} and line 499). A processor JSON names each step by that name",
)
DEFAULT_STEP_NAMES = (
    "rename_observations_processor",
    "to_batch_processor",
    "device_processor",
    "normalizer_processor",
    "unnormalizer_processor",
)
SMOLVLA_PROCESSOR_STEPS = UpstreamRef(
    "smolvla_new_line_processor, tokenizer_processor",
    "VERIFIED",
    src(f"{_SMOLVLA}/processor_smolvla.py", 68),
    "the steps SmolVLA's pre-processor adds to the default ones (lines 68 and 69), under the "
    f"names they register ({src(f'{_PROCESSOR}/newline_task_processor.py', 24)} and "
    f"{src(_TOKENIZER, 55)}). With the default ones, the only steps a SmolVLA may name",
)
SMOLVLA_STEP_NAMES = ("smolvla_new_line_processor", "tokenizer_processor")
PI05_PROCESSOR_STEPS = UpstreamRef(
    "relative_actions_processor, pi05_prepare_state_tokenizer_processor_step, "
    "tokenizer_processor, absolute_actions_processor",
    "VERIFIED",
    src(f"{_PI05}/processor_pi05.py", 123),
    "the steps pi05's processors add to the default ones (to line 151), under the names they "
    f"register ({src(_RELATIVE, 84)}, {src(f'{_PI05}/processor_pi05.py', 42)}, "
    f"{src(_TOKENIZER, 55)} and {src(_RELATIVE, 162)}). With the default ones, the only steps "
    "a pi05 may name",
)
PI05_STEP_NAMES = (
    "relative_actions_processor",
    "pi05_prepare_state_tokenizer_processor_step",
    "tokenizer_processor",
    "absolute_actions_processor",
)
PI0_PROCESSOR_STEPS = UpstreamRef(
    "pi0_new_line_processor, tokenizer_processor, relative_actions_processor, "
    "absolute_actions_processor",
    "VERIFIED",
    src(f"{_PI0}/processor_pi0.py", 127),
    "the steps pi0's processors add to the default ones (to line 154): pi05's, without its "
    "state tokenizer and with a step that ends the task with a newline for PaliGemma's "
    f"tokenizer, under the names they register ({src(f'{_PI0}/processor_pi0.py', 38)}, "
    f"{src(_TOKENIZER, 55)}, {src(_RELATIVE, 84)} and {src(_RELATIVE, 162)}). Its tokenizer is "
    "`google/paligemma-3b-pt-224` (line 141), and its actions are absolute, or relative to the "
    "state and made absolute again when `use_relative_actions` says so "
    f"({src(f'{_PI0}/configuration_pi0.py', 53)}, to line 57). With the default ones, the only "
    "steps a pi0 may name",
)
PI0_STEP_NAMES = (
    "pi0_new_line_processor",
    "tokenizer_processor",
    "relative_actions_processor",
    "absolute_actions_processor",
)
XVLA_PROCESSOR_STEPS = UpstreamRef(
    "tokenizer_processor, xvla_image_to_float, xvla_imagenet_normalize, xvla_add_domain_id, "
    "xvla_image_scale",
    "VERIFIED",
    src(f"{_XVLA}/processor_xvla.py", 62),
    "the steps X-VLA's pre-processor adds to the default ones (to line 70), and "
    "`xvla_image_scale` (line 203), which its own factory does not use, under the names they "
    f"register ({src(_TOKENIZER, 55)}, lines 253, 327 and 404). Each but the tokenizer is a fixed "
    "transform of the data: `xvla_image_to_float` takes an image from 0..255 to 0..1, "
    "`xvla_imagenet_normalize` normalises it by ImageNet's statistics, `xvla_image_scale` takes "
    "it from 0..1 back to 0..255, and `xvla_add_domain_id` hands the model the domain its data "
    "came from (`domain_id`, 0 unless it says otherwise, line 415). That domain picks one of "
    "`num_domains` rows in every domain-aware layer and in the soft prompts "
    f"({src(f'{_XVLA}/soft_transformer.py', 226)} and line 344), and `num_domains` is 30 unless "
    f"config.json says otherwise ({src(f'{_XVLA}/configuration_xvla.py', 113)}), so a domain "
    "past them is refused. With the default ones, the only steps an X-VLA may name",
)
XVLA_STEP_NAMES = (
    "tokenizer_processor",
    "xvla_image_to_float",
    "xvla_imagenet_normalize",
    "xvla_add_domain_id",
    "xvla_image_scale",
)
XVLA_DOMAIN_STEP = "xvla_add_domain_id"
XVLA_DEFAULT_DOMAIN = 0
XVLA_DEFAULT_DOMAINS = 30
"""The step XVLA_PROCESSOR_STEPS reads the domain from, the domain it hands over when its config
names none, and the `num_domains` an X-VLA config has when it names none."""
XVLA_ACTION_MODES = UpstreamRef(
    "X-VLA's action_mode: ee6d, joint, agibot_ee6d, franka_joint7, auto, so101_bimanual",
    "VERIFIED",
    src(f"{_XVLA}/action_hub.py", 113),
    "the spaces X-VLA's head decodes its answer in, chosen by config.json's `action_mode`, lowered "
    f"({src(f'{_XVLA}/modeling_xvla.py', 68)}), and `ee6d` unless it says otherwise "
    f"({src(f'{_XVLA}/configuration_xvla.py', 120)}). `ee6d` and `agibot_ee6d` (line 220) are an "
    "end effector's position, its rotation in six numbers and a gripper, `joint` (line 175) is "
    "two arms of six joints with their grippers put through a sigmoid, `franka_joint7` (line "
    "270) is a Franka Panda's joints and `so101_bimanual` (line 428) two SO-101s. `auto` (line "
    "345, to line 425) is the dataset's own action, the model's answer trimmed back to its "
    "length, which on an SO-101 is the arm's own joint positions. The server serves `auto` "
    "alone, and refuses every other mode before a pin or a weight",
)
XVLA_SERVED_MODE = "auto"
XVLA_DEFAULT_MODE = "ee6d"
"""The one mode XVLA_ACTION_MODES says the server serves, and the one a config that names no
`action_mode` decodes in."""
GROOT_PROCESSOR_STEPS = UpstreamRef(
    "groot_n1_7_pack_inputs_v1, groot_n1_7_vlm_encode_v1, groot_n1_7_action_decode_v1, "
    "groot_action_unpack_unnormalize_v2, relative_actions_processor, absolute_actions_processor",
    "VERIFIED",
    src(_GROOT_PROC, 1254),
    "the steps GR00T N1.7's processors are built from besides the default ones "
    "(`make_groot_pre_post_processors`, to line 1325): the step that packs the cameras, the "
    "state and the task (registered at line 1532), the one that encodes them with the "
    "backbone's own processor (line 2039), and one of two that decode the answer, the one for a "
    "checkpoint whose statistics come grouped by the actions they normalise (line 2299) and the "
    "one for a checkpoint that took its dataset's (line 2463), with the relative and absolute "
    "steps a checkpoint that learned relative actions with no grouped statistics adds (lines "
    "1283 and 1324). With the default ones, the only steps a GR00T may name",
)
GROOT_STEP_NAMES = (
    "groot_n1_7_pack_inputs_v1",
    "groot_n1_7_vlm_encode_v1",
    "groot_n1_7_action_decode_v1",
    "groot_action_unpack_unnormalize_v2",
    "relative_actions_processor",
    "absolute_actions_processor",
)
GROOT_PACK_STEP, GROOT_VLM_STEP, GROOT_DECODE_STEP, GROOT_DATASET_DECODE_STEP = GROOT_STEP_NAMES[:4]
GROOT_BASE_MODEL = UpstreamRef(
    "GrootConfig.base_model_path, nvidia/GR00T-N1.7-3B unless it says otherwise",
    "VERIFIED",
    src(_GROOT_CFG, 280),
    "the GR00T a checkpoint is built from before its own weights replace it: `__post_init__` "
    "fills a missing one with `nvidia/GR00T-N1.7-3B` (lines 382 and 383), which the server "
    "refuses rather than let LeRobot load at no revision, as it refuses a SmolVLA naming no "
    "backbone. `GrootPolicy` builds `GR00TN17.from_pretrained(base_model_path)` "
    f"({src(f'{_GROOT}/modeling_groot.py', 107)}), which `snapshot_download`s the whole "
    f"repository at no revision unless it is a local directory ({src(_GROOT_N17, 902)}, to "
    "line 912) and loads its weights, and the checkpoint's own are loaded over them, strictly "
    "(modeling_groot.py lines 204 to 241). The server pins it, fetches its configs and "
    "safetensors, and hands the build that directory. The backbone is built from a config "
    "LeRobot carries and never fetched (line 897, and lines 353 to 357), but only while the "
    "base's config.json names `nvidia/Cosmos-Reason2-2B` as its `model_name`, or none, which "
    "means the same (line 79, and line 747): any other is fetched as an AutoConfig trusting its "
    "repository's code, at no revision (line 356), so the server reads the pinned base's "
    "config.json before any weight and refuses another backbone",
)
GROOT_BACKBONE = "nvidia/Cosmos-Reason2-2B"
"""The backbone GROOT_BASE_MODEL says LeRobot builds from a config of its own."""
GROOT_VLM_PROCESSOR = UpstreamRef(
    "GrootN17VLMEncodeStep loads its tokenizer and processors with trust_remote_code=True",
    "VERIFIED",
    src(_GROOT_PROC, 1369),
    "`_build_n1_7_processor` loads `AutoTokenizer`, `Qwen2VLImageProcessor` and "
    "`Qwen3VLVideoProcessor` from the step's `model_name` with trust_remote_code=True, hard-wired "
    "(to line 1373), the first time the step runs (`proc`, line 2069, to line 2073), so on a "
    "session's first chunk and not when the server starts. `nvidia/Cosmos-Reason2-2B`, the one "
    "LeRobot's factory names (line 1259), is gated. The server pins `model_name`, fetches its "
    "configs and tokenizer files and no `.py` and no weight, and refuses its directory if it "
    "holds anything else or maps a class to code (`auto_map`), so with trust_remote_code set "
    "there is no code of the repository's own for it to trust",
)
GROOT_DECODE = UpstreamRef(
    "groot_n1_7_action_decode_v1 unnormalises each action group and makes a relative one "
    "absolute against the state the pack step cached",
    "VERIFIED",
    src(_GROOT_PROC, 2299),
    "each group the checkpoint's `modality_config` names is unnormalised by its own statistics "
    "(lines 2362 to 2391), its `rep` and `type` read lowered (`config_value`, utils.py line 88), "
    "and a RELATIVE NON_EEF group has the raw state the pack step cached "
    "added back (lines 2393 to 2416), where an EEF one is a pose composed with xyz+rot6d "
    "(lines 2417 and 2418). With no `raw_stats` or `modality_config` it hands the action back "
    "still normalised (lines 2333 and 2334), and LIBERO's transform makes the gripper -1 or 1 "
    "(line 2280, to 2293), as `action_decode_transform` says, which `libero_sim` gets "
    f"unless it says otherwise ({src(_GROOT_CFG, 393)}, to line 396, and processor_groot.py line "
    "523). A checkpoint that took its dataset's statistics decodes through "
    "`groot_action_unpack_unnormalize_v2` instead, over its state file's `action` min and max "
    "(lines 2488 to 2509), still normalised with no statistics or `normalize_min_max` off, and "
    "with LIBERO's gripper -1 or 1 (lines 2511 to 2518). The server serves a GR00T whose every "
    "group is NON_EEF, with its statistics, and no LIBERO transform",
)
GROOT_NON_EEF = "non_eef"
GROOT_EEF = "eef"
GROOT_LIBERO = "libero"
GROOT_LIBERO_EMBODIMENT = "libero_sim"
GROOT_AUTO_TRANSFORM = "auto"
"""The group type GROOT_DECODE serves and the end effector's it refuses, lowered as LeRobot
reads them (`config_value`, utils.py line 88), LIBERO's transform, the embodiment that gets it
by default, and the `action_decode_transform` that means that default."""
GROOT_WHOLE_CHUNK = UpstreamRef(
    "a relative GR00T's chunk is decoded whole, against the state it was predicted from",
    "VERIFIED",
    src(_GROOT_PROC, 2345),
    "the decode step refuses to decode a relative checkpoint's actions one at a time (to line "
    f"2350), and `GrootPolicy.select_action` refuses a relative policy outright "
    f"({src(f'{_GROOT}/modeling_groot.py', 517)}, to line 523): it is predict_action_chunk and "
    "the post-processor over the whole chunk, which is how the server runs every chunked "
    "policy (POLICY_PIPELINE)",
)
GROOT_PERCENTILE_STATS = UpstreamRef(
    "a relative GR00T keeps its state's percentiles in the pack step's raw_stats",
    "VERIFIED",
    src(f"{_GROOT}/utils.py", 127),
    "the pack step's statistics, the ones its state file holds, are min, max, mean and std "
    "flattened from the grouped ones, with q01 and q99 standing in for min and max under "
    "`use_percentiles` (lines 158 to 164), and its state_dict is those alone "
    f"({src(_GROOT_PROC, 2016)}), so no `observation.state.q01` is where NORMALIZER_STATS looks "
    "and the frame check would go unmade. The grouped statistics are the pack step's "
    "`raw_stats`, the state's and the absolute action's grouped as the actions are (lines "
    "1037 to 1045), so the server reads the state's and the action's q01 and q99 from there, "
    "group by group in `modality_config`'s order, before any weight. A checkpoint that took its "
    "dataset's statistics keeps them in its state file, q01 and q99 among them where the "
    "dataset had them, and is read as every other type's is",
)
GROOT_N1_5_REMOVED = UpstreamRef(
    "LeRobot 0.6.1 serves GR00T N1.7 alone, and refuses N1.5's step names",
    "VERIFIED",
    src(_GROOT_PROC, 2582),
    "N1.5's processors name `groot_pack_inputs_v3`, `groot_eagle_encode_v3`, "
    "`groot_eagle_collate_v3` and `groot_action_unpack_unnormalize_v1` (to line 2587), which "
    "LeRobot registers only as stubs that fail with its removal guidance, and an N1.5 config "
    f"sets `tokenizer_assets_repo` ({src(_GROOT_CFG, 357)}, refused at line 375) or names an "
    "N1.5 base (`infer_groot_model_version`, lines 102 to 116). The server refuses each in a "
    "sentence of its own, before a pin or a weight",
)
GROOT_N1_5_STEP_NAMES = (
    "groot_pack_inputs_v3",
    "groot_eagle_encode_v3",
    "groot_eagle_collate_v3",
    "groot_action_unpack_unnormalize_v1",
)
GROOT_N1_5_BASES = ("gr00t-n1.5", "gr00t_n1.5")
"""What GROOT_N1_5_REMOVED says an N1.5 base path holds, lowered."""
GROOT_IMAGE_FALLBACK = UpstreamRef(
    "GR00T's pack step feeds every camera in alphabetical order when none is one its video "
    "modality keys name",
    "VERIFIED",
    src(_GROOT_PROC, 1563),
    "the pack step orders the views it hands the backbone by the checkpoint's "
    "`video_modality_keys` (to line 1621): a key no camera matches is dropped, so the model sees "
    "fewer views than it learned on, and with none matching it falls back to every camera in "
    "alphabetical order (lines 1587 to 1598). It pads no missing view, so the arm needs a camera "
    "for every image a GR00T looks at, and the server refuses a checkpoint whose modality keys "
    "name an image its config.json does not",
)
GROOT_INFERENCE_PACKAGES = UpstreamRef(
    "a GR00T N1.7 answers with diffusers and dm-tree beside what every family needs, and "
    "LeRobot's own [groot] extra adds the rest for training",
    "VERIFIED",
    src(_GROOT_N17, 466),
    "the action head is built only with diffusers installed (`require_package`, and again in "
    "`action_head/cross_attention_dit.py` lines 52 and 112), and every step's inputs go through "
    f"dm-tree (line 853). LeRobot's `[groot]` extra ({src('pyproject.toml', 219)}) adds peft, "
    "timm, decord and its `[dataset]` extra, which brings datasets, pyarrow, pandas, av and "
    "torchcodec, for training: its own note (line 223) says the dataset is built for "
    "relative-action training statistics, and processor_groot.py requires datasets only in "
    f"`_make_relative_action_training_stats_from_dataset_meta` ({src(_GROOT_PROC, 857)}, line "
    "867). So the server's extra brings diffusers and dm-tree, about 25 MB, rather than "
    "LeRobot's `[groot]`, about 340 MB, and the server refuses a GR00T before a pin or a weight "
    "when either is missing, since LeRobot would find out only once the model is built",
)
GROOT_INFERENCE_MODULES = ("diffusers", "tree")
"""The import names GROOT_INFERENCE_PACKAGES says a GR00T answers with: diffusers, and dm-tree
as `tree`."""
XVLA_LIBERO_ROTATION = UpstreamRef(
    "xvla_rotation_6d_to_axis_angle turns an end-effector rotation into axis-angle and the "
    "gripper into -1 or 1",
    "VERIFIED",
    src(f"{_XVLA}/processor_xvla.py", 450),
    "the post-processor step LIBERO's X-VLA is built with (`make_xvla_libero_pre_post_processors`, "
    "line 515, to line 535): it reads an end effector's position and rotation out of the action, "
    "turns the rotation into an axis and an angle and the gripper into -1 or 1 (to line 500), "
    "for LIBERO's simulator. No served type allows it, and the server refuses it in a sentence "
    "of its own",
)
XVLA_STRICT_LOAD = UpstreamRef(
    "XVLAPolicy.from_pretrained loads its weights strictly",
    "VERIFIED",
    src(f"{_XVLA}/modeling_xvla.py", 424),
    "whatever `strict` it is passed (line 436), it loads its state dict with strict=True (line "
    "506), once it has put back the tied embedding a save drops (lines 497 to 504), so the "
    "server's strict=True asks nothing more of it. Its Florence-2 is built from config.json's "
    f"`florence_config` ({src(f'{_XVLA}/configuration_xvla.py', 168)}, and line 86 here), never "
    "fetched, so the only model an X-VLA names is its tokenizer's, `facebook/bart-large` unless "
    "it says otherwise (configuration_xvla.py line 103)",
)
DEVICE_OVERRIDE = UpstreamRef(
    'overrides={"device_processor": {"device": device}}',
    "VERIFIED",
    src("src/lerobot/rollout/context.py", 472),
    "how upstream's own loops put a loaded pre-processor on the device they run on (and "
    f"{src('src/lerobot/scripts/lerobot_eval.py', 772)}): an override keyed by the step's "
    f"registry name, merged over its saved config ({src(_PIPELINE, 1145)}). The server puts "
    "the post-processor's device step on the CPU, so the actions it answers with are there",
)
DEVICE_STEP = "device_processor"
AUTO_DEVICE = UpstreamRef(
    "lerobot.utils.device_utils.auto_select_torch_device()",
    "VERIFIED",
    src(_DEVICE_UTILS, 22),
    "cuda, then mps, then xpu, then the CPU: the device the policy server loads a checkpoint "
    "on, and what it reports as having a GPU or not",
)
PROCESSOR_RESET = UpstreamRef(
    "DataProcessorPipeline.reset()",
    "VERIFIED",
    src(_PIPELINE, 1604),
    "resets every step that keeps state. The server resets the policy and both processors at "
    "every session's reset, as upstream's sync inference does "
    f"({src('src/lerobot/rollout/inference/sync.py', 90)})",
)
NORMALIZER_STATS = UpstreamRef(
    "NormalizerProcessorStep.state_dict() -> {'<feature>.<stat>': Tensor}",
    "VERIFIED",
    src(_NORMALIZE, 176),
    "the statistics a checkpoint was normalised with, flat, `observation.state.q01` and "
    f"`.q99` among them where the dataset had quantiles ({src(_NORMALIZE, 387)}). The server "
    "reports the state's and the action's 1st and 99th percentiles, which is how the arm's side "
    "tells whether the policy learned from an arm calibrated like this one",
)
QUANTILE_STATS = ("q01", "q99")
ACTION_FEATURE_NAMES = UpstreamRef(
    "PI05Config.action_feature_names, a list of names or None",
    "VERIFIED",
    src(_PI05_CFG, 57),
    "the names of the action's dimensions, filled from the dataset by make_policy "
    f"({src(_FACTORY, 309)}) on the policies that have the field, pi0's among them "
    f"({src(f'{_PI0}/configuration_pi0.py', 57)}). The server reports them where a checkpoint "
    "has them, and the arm's side checks them against its bus's motors",
)
MISSING_IMAGES_PADDED = UpstreamRef(
    "SmolVLA, pi05, pi0 and X-VLA run with some of their images missing",
    "VERIFIED",
    src(f"{_SMOLVLA}/modeling_smolvla.py", 340),
    "an image key with nothing under it is left out (SmolVLA pads up to `empty_cameras` of "
    "them, line 373), and only all of them missing raises (line 343, pi05's "
    f"{src(f'{_PI05}/modeling_pi05.py', 965)}, and pi0's "
    f"{src(f'{_PI0}/modeling_pi0.py', 995)}, which pads each missing one with -1 and a mask of "
    "zero, to line 1044). X-VLA stacks the images it is given and pads them out to "
    f"`num_image_views` with zeros and a mask of zero ({src(f'{_XVLA}/modeling_xvla.py', 311)}, "
    "to line 341, only all of them missing raising). ACT takes every image it names, and so "
    "does GR00T (GROOT_IMAGE_FALLBACK) "
    f"(`predict_action_chunk` stacks each, {src(_ACT_MODEL, 131)}), so the arm's side "
    "refuses an ACT with an image no camera is mapped to, and says which of the others' go "
    "padded",
)
ACT_BACKBONE_WEIGHTS = UpstreamRef(
    'ACTConfig.pretrained_backbone_weights = "ResNet18_Weights.IMAGENET1K_V1"',
    "VERIFIED",
    src(_ACT_CFG, 98),
    "torchvision fetches these ImageNet weights when ACT is built "
    f"({src(_ACT_MODEL, 328)}), before the checkpoint's own weights replace them. The server "
    "sets it to None, so building an ACT fetches nothing the checkpoint did not name",
)

RELATIVE_ACTIONS = UpstreamRef(
    "RelativeActionsProcessorStep caches the state each time the pre-processor runs",
    "VERIFIED",
    src(_RELATIVE, 125),
    "training makes a chunk relative to one state, broadcast across it (`to_relative_actions`, "
    "line 40), and AbsoluteActionsProcessorStep (line 164) adds back the state the relative "
    "step cached last (line 131). LeRobot's own loop runs the pre-processor, select_action and "
    f"the post-processor every tick ({src(_SYNC, 114)}), so each action it plays from its queue "
    "is made absolute against the state of the tick it is played at, not the one it was "
    "predicted from. The policy server runs the post-processor over a whole chunk at once, "
    "against the state the chunk was predicted from, as OpenPI's AbsoluteActions does, and a "
    "pi05 or a pi0 that learned relative actions is served in chunks, not asked every tick",
)
PI05_FROM_PRETRAINED = UpstreamRef(
    "PI05Policy.from_pretrained returns the model without its weights when they do not load",
    "VERIFIED",
    src(f"{_PI05}/modeling_pi05.py", 747),
    "pi05 overrides the loader: a model.safetensors it cannot read returns the model as it was "
    "built (line 813), and a load_state_dict that raises is caught and printed "
    "(line 859), so a pi05 whose weights did not load comes back as a random network with only a "
    "printed line to say so. The policy server loads a pi05's weights as that loader does, "
    "`_fix_pytorch_state_dict_keys` (line 864) and a `model.` prefix, strictly and with nothing "
    "caught",
)
PI0_FROM_PRETRAINED = UpstreamRef(
    "PI0Policy.from_pretrained returns the model without its weights when they do not load",
    "VERIFIED",
    src(f"{_PI0}/modeling_pi0.py", 782),
    "pi0's loader is pi05's: a model.safetensors it cannot read returns the model as it was "
    "built (lines 845 to 848), and a load_state_dict that raises is caught and printed (lines "
    "894 and 895), so a pi0 whose weights did not load comes back as a random network. The "
    "policy server loads a pi0's weights the way it loads a pi05's, with pi0's own "
    "`_fix_pytorch_state_dict_keys` (line 899) and a `model.` prefix, strictly and with nothing "
    "caught",
)
PI0_FAST_REMOTE_CODE = UpstreamRef(
    "PI0FastPolicy loads the FAST tokenizer with trust_remote_code=True",
    "VERIFIED",
    src(f"{_PI0_FAST}/modeling_pi0_fast.py", 764),
    "`PI0FastPolicy.__init__` loads `AutoProcessor.from_pretrained(config.action_tokenizer_name, "
    "trust_remote_code=True)` (to line 766), hard-wired in the policy rather than in a "
    "processor step an override could reach, so the FAST tokenizer's repository runs its own "
    "Python wherever a pi0_fast is built. `action_tokenizer_name` is "
    "`lerobot/fast-action-tokenizer` unless a config says otherwise "
    f"({src(f'{_PI0_FAST}/configuration_pi0_fast.py', 63)}), and the type registers as "
    "`pi0_fast` at line 28. The server refuses the type before it reads anything past "
    "config.json",
)

# ── LeRobot's own policy server, and why quackd has one of its own ──────────────────────

ASYNC_PICKLE = UpstreamRef(
    "async inference unpickles what it is sent",
    "VERIFIED",
    src(f"{_ASYNC}/policy_server.py", 183),
    "the policy server `pickle.loads` every observation (and the policy spec, line 125), and "
    "the robot client unpickles every chunk it gets back (robot_client.py line 286), over "
    "`add_insecure_port` (line 428), so anything that can reach the port runs code on the "
    "other side. quackd's protocol is JSON with every number checked, behind a token",
)
ASYNC_CLIENT_NEEDS_TORCH = UpstreamRef(
    "the async robot client imports torch",
    "VERIFIED",
    src(f"{_ASYNC}/robot_client.py", 48),
    "and the process that owns the serial bus is the one quackd keeps free of torch",
)
ASYNC_SKIPS_SIMILAR = UpstreamRef(
    "observations_similar(obs1, obs2, lerobot_features, atol=1)",
    "VERIFIED",
    src(f"{_ASYNC}/helpers.py", 281),
    "the async server skips an observation whose joint state is within a norm of 1 of the "
    "last one it ran, which is how a policy stops seeing an arm that is barely moving. quackd's "
    "server runs every step it is asked",
)
ASYNC_SUPPORTED_POLICIES = UpstreamRef(
    'SUPPORTED_POLICIES = ["act", "smolvla", "diffusion", "tdmpc", "vqbet", "pi0", "pi05", '
    '"groot"]',
    "VERIFIED",
    src(f"{_ASYNC}/constants.py", 26),
    "the policies the async server will load, and a policy type outside it is refused there",
)

POLICY_PIPELINE = UpstreamRef(
    "POLICY_PIPELINE: build_inference_frame, the pre-processor, predict_action_chunk, the "
    "post-processor over the chunk, make_robot_action a row at a time",
    "VERIFIED",
    src(_FACTORY, 150),
    "exercised, not only read: CI's torch job builds a tiny random ACT (built with backbone "
    "weights None and saved naming LeRobot's default, both processors saved), puts it in a Hub "
    "cache at a commit and a tag, and serves it with HF_HUB_OFFLINE=1 and torchvision's fetch "
    "failing, through the real server and the real client (tests/test_policy_pipeline.py). "
    "That runs CHECKPOINT_FILES fetched at a revision, PreTrainedConfig.from_pretrained on "
    "them, ACT_BACKBONE_WEIGHTS set to None, get_policy_class and from_pretrained with the "
    "config and strict=True, make_pre_post_processors with DEVICE_OVERRIDE, "
    "build_inference_frame, predict_action_chunk cut to n_action_steps, the post-processor "
    "over the whole chunk, make_robot_action per row with every row checked against what "
    "select_action plays, the resets of the policy and both processors, the normaliser's "
    "quantiles, and the rate read from TRAIN_DATASET. Beside it the job serves a tiny random "
    "X-VLA in `auto` mode, offline, its Florence-2 built from its config and a tokenizer of a "
    "few words pinned at a commit in a Hub cache of its own, which runs the per-type allowlist, "
    "a pin's TOKENIZER_FILES fetched from the cache alone, check_pinned on them, "
    "XVLA_STRICT_LOAD, its processors with the pinned tokenizer, and a chunk back through the "
    "real client. It runs a tiny ACT and a tiny X-VLA and nothing else, on the CPU, and random "
    "ones: nothing about what a trained policy does to an arm follows from it",
)

# ── UNVERIFIED: our assumptions, and what quackd does about each ────────────────────────

VLA_PIPELINE = UpstreamRef(
    "VLA_PIPELINE",
    "UNVERIFIED",
    src(f"{_SMOLVLA}/processor_smolvla.py", 68),
    "that SmolVLA, pi05, pi0 and X-VLA run through the same pipeline as ACT: their tokenizer "
    "step, the nested repositories pinned with --pin and fetched without any code in them, the "
    "images a camera does not give left padded, their weights loaded strictly (SmolVLA's and "
    "X-VLA's by LeRobot's own loaders, XVLA_STRICT_LOAD, pi05's and pi0's by quackd's copy of "
    "theirs, PI05_FROM_PRETRAINED and PI0_FROM_PRETRAINED), so a checkpoint saved with weights "
    "its model does not have would be "
    "refused, and pi05's and pi0's relative actions made absolute over a whole chunk at once, "
    "against the state it was predicted from. That last one is not what LeRobot's own loop "
    "does (RELATIVE_ACTIONS), and it is how training made the chunk relative, so a trained "
    "pi05 or pi0 is taken to want it. That GR00T N1.7 runs through it too, with its "
    "processors' own decoding, its pinned base and its backbone's pinned tokenizer, and its "
    "frame read from the pack step's raw_stats (GROOT_PERCENTILE_STATS). No job runs a SmolVLA, "
    "a pi05, a pi0 or a GR00T, and the X-VLA CI's job serves is a tiny random one "
    "(POLICY_PIPELINE), so no trained one of the five has run anywhere. An X-VLA is served in its "
    "`auto` action mode alone (XVLA_ACTION_MODES), taken to answer in the joints of the arm it "
    "learned from. quackd loads them through the allowlist each type has and the pins and "
    "refusals ACT's go through, says in /v1/policy which images go padded, and a first run "
    "should be on the simulator with `quackd policy check --bench` first",
)
TICK_MODE = UpstreamRef(
    "TICK_MODE",
    "UNVERIFIED",
    src(_POLICY, 280),
    "that an ACT with temporal_ensemble_coeff set, asked through select_action every tick and "
    "its one action put through the post-processor, answers what upstream's own loop would "
    "play. The server refuses one without a GPU, because it has to answer inside a tick, so "
    "CI's CPU job cannot run it, and no bench has",
)
LOAD_POLICY = UpstreamRef(
    "LOAD_POLICY",
    "UNVERIFIED",
    src(_UTILS, 141),
    "load_policy() in the arm's backend builds a policy object from verified names in the "
    "arm's own process, the one no checkpoint is to load in, and hands the pre-processor a "
    "raw observation that build_inference_frame would have shaped first. Nothing in quackd "
    "calls it, no test runs it past the missing extra, and the policy server is the path a "
    "checkpoint takes to the arm. What reaches the arm from any policy is quackd's rule and "
    "not upstream's: the verbs' step cap, and a goal outside the travel clipped and counted, "
    "unlike a verb's goal, which is refused (ADR-0036)",
)


def all_refs() -> list[UpstreamRef]:
    return [v for v in globals().values() if isinstance(v, UpstreamRef)]


def refs_by_status(status: str) -> list[UpstreamRef]:
    return [r for r in all_refs() if r.status == status]
