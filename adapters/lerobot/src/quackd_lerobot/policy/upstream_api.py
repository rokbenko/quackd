"""The only file in quackd allowed to spell a LeRobot policy name (ADR-0022).

Every constant is tagged VERIFIED (read from upstream source at the pin, link given) or
UNVERIFIED (an assumption of ours, with what quackd does about it).
`docs/adapters/lerobot/README.md` is the human-readable version; `tests/test_upstream_api.py` proves
UNVERIFIED names are only reachable from the files that live with them.

These are read against lerobot 0.7.0, at the commit its `v0.7.0` tag names
(410c095eacc0011d998a73383eb94abed2715af0), the commit the arm's own refs are pinned to as well
(`quackd_lerobot.upstream_api.PIN`). A policy runs where a checkpoint was trained and exported,
and its processors and its config are read by whatever lerobot the policy server has installed,
so the release people install is the one to read. The installed 0.7.0 wheel and the tag were
compared file by file on the day they were read, and every file these rows cite that the wheel
carries was the same; `pyproject.toml` and `remote_inference/README.md`, which it does not
carry, were read at the tag. The rows were first read at 0.6.1, and a row whose thing 0.7.0
changed or removed says so.

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
PIN = "410c095eacc0011d998a73383eb94abed2715af0"  # the v0.7.0 tag
VERSION = "0.7.0"
READ_ON = "2026-11-08"


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
_REMOTE = "src/lerobot/remote_inference"
_CODEC = "src/lerobot/transport/protocol/codec.py"
_TYPES = "src/lerobot/lerobot_types.py"
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
_SYNC = "src/lerobot/inference/sync.py"

# ── a policy (moved here from the arm's own file, read at 0.6.1 and again at 0.7.0) ─────

POLICY_BASE = UpstreamRef(
    "lerobot.policies.pretrained.PreTrainedPolicy", "VERIFIED", src(_POLICY, 73)
)
PRETRAINED_CONFIG = UpstreamRef(
    "lerobot.configs.policies.PreTrainedConfig",
    "VERIFIED",
    src(_POLICY_CFG, 42),
    "`class PreTrainedConfig(draccus.ChoiceRegistry, HubMixin, abc.ABC)`, with "
    "`from_pretrained(pretrained_name_or_path, *, ...)` (line 178), a `device` field (line 63) "
    "and a `type` property (line 105). A checkpoint's config is read through it before the "
    "policy class is built, so it is the one policy name that is not in the factory. New at "
    "0.7.0: a `dtype` field, a `torch.dtype` or None (line 65, checked at line 89), which a "
    "dtype's name in config.json decodes to "
    f"({src('src/lerobot/configs/parser.py', 55)}, to line 66), registered when "
    "`lerobot.configs` is imported, as reading a config imports it; pi05's, pi0's and X-VLA's "
    "own `dtype` was a string at 0.6.1, and a config.json that says `float32` still reads. And "
    "`from_pretrained` now sets the config's `pretrained_path` and `pretrained_revision` to "
    "what it was read from (lines 246 and 247), which no code of the six types the server "
    "serves reads. The server reads the config as LeRobot does and sets only what `pipeline.py` "
    "says it sets",
)
CONFIG_HAS_NO_RATE = UpstreamRef(
    "a policy's config carries no fps",
    "VERIFIED",
    src(_POLICY_CFG, 42),
    "nothing in PreTrainedConfig or the policy configs that extend it says how many actions a "
    "second the policy was trained to run at: that is the fps of the dataset it learned from. "
    "So the policy server takes a rate it is given or reads one from where it names, and never "
    "guesses one, and the rate travels to the arm with the source it came from",
)
POLICY_FROM_PRETRAINED = UpstreamRef(
    "PreTrainedPolicy.from_pretrained(path, *, config=None, local_files_only=False, "
    "revision=None, strict=False)",
    "VERIFIED",
    src(_POLICY, 160),
    "a local directory or a Hub repo id, at a revision when one is given; the policy comes back "
    "in eval mode (line 219). Left lenient, a weight the file lacks or one it has that the model "
    "does not is only logged (`_load_as_safetensor`, line 223), and the model keeps what it was "
    "built with there, so the policy server asks for strict=True and refuses a checkpoint whose "
    "weights are not its model's. New at 0.7.0: a config with no `device` raises (lines 193 to "
    "196), which the server never meets, since it sets the device before it builds",
)
POLICY_SELECT_ACTION = UpstreamRef(
    "PreTrainedPolicy.select_action(batch: dict[str, Tensor]) -> Tensor, or PolicyOutput",
    "VERIFIED",
    src(_POLICY, 406),
    "one action per call, the policy handles its own action-chunk cache. Since 0.7.0 it may "
    "answer a PolicyOutput, the action beside what the policy predicts besides it (lines 414 to "
    "420), where 0.6.1's answered a tensor (POLICY_OUTPUT)",
)
POLICY_PREDICT_ACTION_CHUNK = UpstreamRef(
    "PreTrainedPolicy.predict_action_chunk(batch: dict[str, Tensor]) -> Tensor, or PolicyOutput",
    "VERIFIED",
    src(_POLICY, 390),
    "the whole chunk for one observation, which is what the policy server answers a step with, "
    "one action per tick from the tick the observation was read at. Since 0.7.0 it may answer a "
    "PolicyOutput as select_action may (line 401, POLICY_OUTPUT), and a policy that takes a "
    "`noise` keyword starts its sampling from it (line 398); the server passes none, so each "
    "draws its own",
)
POLICY_OUTPUT = UpstreamRef(
    'PolicyOutput, {"action": Tensor, "prediction": ...}, which select_action and '
    "predict_action_chunk may answer in place of a tensor",
    "VERIFIED",
    src(_TYPES, 75),
    "a TypedDict whose `action` is the action and whose `prediction`, which it need not carry, "
    "is what the policy predicts besides (lines 82 and 83), new at 0.7.0, where both of "
    f"PreTrainedPolicy's methods answer `Tensor | PolicyOutput` ({src(_POLICY, 392)} and line "
    "408). LeRobot's own loop takes the action out of either with `policy_output_to_transition` "
    f"({src(_SYNC, 123)}; {src(f'{_PROCESSOR}/converters.py', 358)}). Of LeRobot's policies "
    "g05 answers one when it predicts something besides its action "
    f"({src('src/lerobot/policies/g05/modeling_g05.py', 1969)} and line 1992), and so does "
    "lingbot_va with `return_predicted_video` set "
    f"({src('src/lerobot/policies/lingbot_va/modeling_lingbot_va.py', 460)}); none of the six "
    "types the server serves does, each annotating its two methods `-> Tensor` and naming no "
    "PolicyOutput in its modeling file. The server takes the `action` of one all the same "
    "(`pipeline.py`), so a policy that answered one would be served its action and none of its "
    "prediction",
)
POLICY_RESET = UpstreamRef("PreTrainedPolicy.reset()", "VERIFIED", src(_POLICY, 238))
GET_POLICY_CLASS = UpstreamRef(
    "lerobot.policies.factory.get_policy_class(name)", "VERIFIED", src(_FACTORY, 62)
)
MAKE_PRE_POST_PROCESSORS = UpstreamRef(
    "lerobot.policies.factory.make_pre_post_processors(policy_cfg, pretrained_path=None, "
    "pretrained_revision=None)",
    "VERIFIED",
    src(_FACTORY, 158),
    "a raw observation goes through the pre-processor and the action tensor through the "
    "post-processor before it is a RobotAction, and both load from the checkpoint at the "
    "revision given. Given a path, 0.7.0 first asks the type's own processor module for a "
    "`make_<type>_pre_post_processors_from_pretrained` (lines 195 to 207, and lines 486 to "
    "516), which GR00T alone of the types served has, and otherwise loads the saved JSONs "
    "with the overrides (`load_pretrained_policy_processors`, "
    f"{src(f'{_PROCESSOR}/factory.py', 205)}), as 0.6.1 did. GR00T's builds its processors "
    f"from its config only for a raw N1.7 checkpoint ({src(_GROOT_PROC, 497)}), one whose "
    f"config.json names no `type` ({src(_GROOT_CFG, 135)}), which the server refuses, and loads "
    "the saved JSONs for every other (processor_groot.py line 516), so the JSONs the server "
    "checked are the ones every served type loads",
)
MAKE_POLICY = UpstreamRef(
    "lerobot.policies.factory.make_policy(cfg)", "VERIFIED", src(_FACTORY, 255)
)
BUILD_INFERENCE_FRAME = UpstreamRef(
    "lerobot.policies.utils.build_inference_frame(observation, device, ds_features, task, "
    "robot_type)",
    "VERIFIED",
    src(_UTILS, 158),
    "picks the keys `ds_features` names out of a raw observation and makes them tensors on the "
    "device, which is why the server builds `ds_features` from the motor names and the cameras "
    "a reset declares rather than from the checkpoint alone",
)
MAKE_ROBOT_ACTION = UpstreamRef(
    "lerobot.policies.utils.make_robot_action(action_tensor, ds_features)",
    "VERIFIED",
    src(_UTILS, 192),
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
    "`n_action_steps` may not exceed `chunk_size` (line 144). SmolVLA's defaults are 50 and 50 "
    f"({src(_SMOLVLA_CFG, 29)}), and so are pi0's ({src(f'{_PI0}/configuration_pi0.py', 38)}). "
    f"X-VLA's are 32 and 32 ({src(f'{_XVLA}/configuration_xvla.py', 91)}), and GR00T's 40 and "
    f"40 ({src(_GROOT_CFG, 251)}). The policy server reports both",
)
TEMPORAL_ENSEMBLE = UpstreamRef(
    "ACTConfig.temporal_ensemble_coeff",
    "VERIFIED",
    src(_ACT_CFG, 119),
    "None by default. Set, ACT is asked every step and `n_action_steps` must be 1 (line 139), "
    "which is a policy asked every tick: the loop's tick mode",
)

# ── a checkpoint is code (why no quackd command loads one beside the arm's bus) ─────────

PROCESSOR_CLASS_IMPORT = UpstreamRef(
    "a processor step named by class is imported by its module path",
    "VERIFIED",
    src(_PIPELINE, 1199),
    "PolicyProcessorPipeline.from_pretrained resolves each step of a processor's JSON by its "
    "`registry_name`, or else imports whatever `module.Class` its `class` key names with "
    "importlib, so loading a checkpoint's processors can run any code the checkpoint points "
    "at. This is why no quackd command loads a checkpoint in the process that owns the serial "
    "bus. Only `load_policy()` would (LOAD_POLICY), and nothing in quackd calls it",
)
TOKENIZER_TRUSTS_REMOTE_CODE = UpstreamRef(
    "ActionTokenizerProcessorStep.trust_remote_code defaults to True",
    "VERIFIED",
    src(_TOKENIZER, 359),
    "the action tokenizer step (`action_tokenizer_processor`, line 338) loads its tokenizer "
    "trusting the repository's own code unless told not to. The observation tokenizer "
    "SmolVLA, pi05, pi0 and X-VLA use (`tokenizer_processor`, line 57) loads one by "
    "`tokenizer_name` at no revision (line 113), and SmolVLA names its backbone by an unpinned "
    f"Hub name (`vlm_model_name`, {src(_SMOLVLA_CFG, 85)}). The policy server allows no action "
    "tokenizer, forces trust_remote_code False on any step that has it, and pins every such "
    "name at a commit or a tag or refuses it (`pipeline.py`), and it refuses a step that keeps "
    "its tokenizer in a directory inside the checkpoint instead, which LeRobot puts in place of "
    "whatever the step names (PROCESSOR_ARTIFACTS). A SmolVLA config that leaves "
    "`vlm_model_name` out gets that default filled in, so it is refused. SmolVLA's build asks "
    "transformers for its backbone without saying trust_remote_code either way, or which "
    f"attention to build it with ({src(_SMOLVLM, 92)}, to line 101), so a pinned model whose "
    "files map a class to code (`auto_map`), or name a repository as their attention "
    "(`attn_implementation`), a kernel on the Hub that transformers 5 would run once the kernels "
    "package is installed, or whose directory in the Hub's cache holds anything but configs, "
    "tokenizer files and safetensors, is refused",
)
PROCESSOR_ARTIFACTS = UpstreamRef(
    "a processor step's artifacts: directories inside the checkpoint that LeRobot loads in "
    "place of the config keys they name",
    "VERIFIED",
    src(_PIPELINE, 1058),
    "new at 0.7.0. A save asks every step for its `save_artifacts` and writes what the step "
    "saved into its config, and an `artifacts` mapping of config key to path beside it (lines "
    "572 to 586); a load puts each path, inside the checkpoint's own directory, in its key's "
    "place before the step is built (lines 1058 to 1114, from line 1035), downloading it whole "
    "from a Hub repository and nothing from a local one, and merges the overrides over that "
    "(line 1259). The tokenizer step saves its tokenizer that way on every save, as "
    f"`tokenizer` beside the JSON ({src(_TOKENIZER, 298)}, to line 305), so every SmolVLA, "
    "pi05, pi0 and X-VLA LeRobot 0.7.0 saves names its tokenizer `tokenizer` and declares it "
    "in `artifacts`, where a 0.6.1 save names the Hub repository and has no artifacts. quackd "
    "fetches no such directory and checks none, and a step whose `artifacts` named a model its "
    "config did not would load that directory with no pin, so the server refuses any step "
    "that carries `artifacts`, before a pin or a weight (`pipeline.ARTIFACTS_KEY`). A "
    "checkpoint LeRobot 0.7.0 saved with a tokenizer step is refused until quackd fetches and "
    "checks a checkpoint's own artifact directories",
)

# ── loading a checkpoint, and running a step through it (`pipeline.py`) ─────────────────

CHECKPOINT_FILES = UpstreamRef(
    "config.json, model.safetensors, policy_preprocessor.json, policy_postprocessor.json, "
    "train_config.json",
    "VERIFIED",
    src(_CONSTANTS, 82),
    "the files a checkpoint is read from: the config (`CONFIG_NAME`, "
    f"{src(_POLICY_CFG, 194)}), the weights (`SAFETENSORS_SINGLE_FILE`, {src(_POLICY, 199)}), "
    "the two processors under their default names (lines 82 and 83), and the training run's "
    f"config (`TRAIN_CONFIG_NAME`, {src(_TRAIN_CFG, 44)}). The first two names are "
    "huggingface_hub's constants, the same in the 1.33.0 `uv.lock` holds and in the 2.4.0 CI's "
    "policy job installs. The policy server fetches each at the revision named, the config and "
    "the processors before anything else, and nothing else of the repository",
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
    src(_DEFAULT_CFG, 35),
    f"the dataset a policy was trained on (`dataset: DatasetConfig`, {src(_TRAIN_CFG, 127)}), "
    "by its Hub id and a revision that may be None (line 48), and the rate that dataset was "
    f"recorded at, the `fps` of its `meta/info.json` (`INFO_PATH`, {src(_DATASET_UTILS, 96)}, "
    "and `DatasetInfo.fps`, line 197). No config carries a rate (CONFIG_HAS_NO_RATE), so this "
    "is where the policy server reads one when it is given no --fps, and only at a commit or a "
    "tag: the checkpoint chose that revision, and a branch would give a rate that could change "
    "between two serves",
)
DATASET_INFO_FILE = "meta/info.json"
FEATURE_KEYS = UpstreamRef(
    "observation.state, observation.images.<name>, action",
    "VERIFIED",
    src(_CONSTANTS, 24),
    "the keys a policy's state, its images and its action go by (`OBS_STATE`, `OBS_IMAGES` "
    "line 26, `ACTION` line 35). build_inference_frame takes an image's frame from the raw "
    "observation under its key with `observation.images.` cut off "
    f"({src('src/lerobot/utils/feature_utils.py', 135)}), so the server hands it each camera's "
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
    f"({src(_SMOLVLA_CFG, 24)}), PI05Config ({src(_PI05_CFG, 30)}), PI0Config "
    f"({src(f'{_PI0}/configuration_pi0.py', 30)}), XVLAConfig "
    f"({src(f'{_XVLA}/configuration_xvla.py', 78)}) and GrootConfig ({src(_GROOT_CFG, 244)}) "
    "register it. These are the types whose processors the policy server's allowlist was read "
    "from, each its own, and any other type is refused before a byte of its weights is fetched",
)
SERVED_TYPES = ("act", "smolvla", "pi05", "pi0", "xvla", "groot")
DEFAULT_PROCESSOR_STEPS = UpstreamRef(
    "rename_observations_processor, to_batch_processor, device_processor, normalizer_processor, "
    "unnormalizer_processor",
    "VERIFIED",
    src(f"{_PROCESSOR}/factory.py", 104),
    "LeRobot's default steps (make_default_policy_processor_steps), "
    "and all of ACT's (line 167), under the names they register "
    f"({src(f'{_PROCESSOR}/rename_processor.py', 26)}, "
    f"{src(f'{_PROCESSOR}/batch_processor.py', 211)}, {src(_DEVICE, 34)}, "
    f"{src(_NORMALIZE, 428)} and line 502). Every served type's processors are built from all "
    f"five but GR00T N1.7's, which leave the normaliser pair out ({src(_GROOT_PROC, 368)}). A "
    "processor JSON names each step by that name. New at 0.7.0: building them raises unless "
    "the config's device and features are set (lines 119 to 123), which the server never "
    "meets, since it loads a checkpoint's saved processors and builds none",
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
    f"{src(_TOKENIZER, 57)}). With the default ones, the only steps a SmolVLA may name",
)
SMOLVLA_STEP_NAMES = ("smolvla_new_line_processor", "tokenizer_processor")
PI05_PROCESSOR_STEPS = UpstreamRef(
    "relative_actions_processor, pi05_prepare_state_tokenizer_processor_step, "
    "tokenizer_processor, absolute_actions_processor",
    "VERIFIED",
    src(f"{_PI05}/processor_pi05.py", 137),
    "the steps pi05's processors add to the default ones (to line 168), under the names they "
    f"register ({src(_RELATIVE, 102)}, {src(f'{_PI05}/processor_pi05.py', 42)}, "
    f"{src(_TOKENIZER, 57)} and {src(_RELATIVE, 195)}). With the default ones, the only steps "
    "a pi05 may name. Changed at 0.7.0: the tokenizer step names `text_tokenizer_name` "
    "(line 158, TEXT_TOKENIZER_NAME) where 0.6.1 wrote PaliGemma's, and the state step leaves "
    "the state out of the prompt for a pi05 with proprioceptive memory (line 155, PI05_MEMORY)",
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
    f"{src(_TOKENIZER, 57)}, {src(_RELATIVE, 102)} and {src(_RELATIVE, 195)}). Its tokenizer is "
    "`text_tokenizer_name` (line 141, TEXT_TOKENIZER_NAME), `google/paligemma-3b-pt-224` unless "
    "a config says otherwise, where 0.6.1 wrote PaliGemma's, and its actions are absolute, or "
    "relative to the state and made absolute again when `use_relative_actions` says so "
    f"({src(f'{_PI0}/configuration_pi0.py', 55)}, to line 59). With the default ones, the only "
    "steps a pi0 may name",
)
PI0_STEP_NAMES = (
    "pi0_new_line_processor",
    "tokenizer_processor",
    "relative_actions_processor",
    "absolute_actions_processor",
)
TEXT_TOKENIZER_NAME = UpstreamRef(
    "PI05Config.text_tokenizer_name and PI0Config.text_tokenizer_name, "
    "google/paligemma-3b-pt-224 unless a config says otherwise",
    "VERIFIED",
    src(_PI05_CFG, 90),
    f"new at 0.7.0, and pi0's is {src(f'{_PI0}/configuration_pi0.py', 107)}. pi05's and pi0's "
    "processor factories name their tokenizer step's tokenizer by it (processor_pi05.py line "
    "158, processor_pi0.py line 141), where 0.6.1's wrote PaliGemma's name, and no other code "
    "of either reads it: a factory builds a checkpoint's processors when it is trained, and a "
    "served checkpoint's tokenizer is the one its saved tokenizer step names "
    "(MAKE_PRE_POST_PROCESSORS). The server pins that name at a commit or a tag or refuses it, "
    "and refuses a step that keeps its tokenizer inside the checkpoint instead "
    "(PROCESSOR_ARTIFACTS), so a config that names another tokenizer changes nothing the "
    "server loads",
)
XVLA_PROCESSOR_STEPS = UpstreamRef(
    "tokenizer_processor, xvla_image_to_float, xvla_imagenet_normalize, xvla_add_domain_id, "
    "xvla_image_scale",
    "VERIFIED",
    src(f"{_XVLA}/processor_xvla.py", 66),
    "the steps X-VLA's pre-processor adds to the default ones (to line 74), and "
    "`xvla_image_scale` (line 179), which its own factory does not use, under the names they "
    f"register ({src(_TOKENIZER, 57)}, lines 229, 303 and 380). Each but the tokenizer is a fixed "
    "transform of the data: `xvla_image_to_float` takes an image from 0..255 to 0..1, "
    "`xvla_imagenet_normalize` normalises it by ImageNet's statistics, `xvla_image_scale` takes "
    "it from 0..1 back to 0..255, and `xvla_add_domain_id` hands the model the domain its data "
    "came from (`domain_id`, 0 unless it says otherwise, line 391). That domain picks one of "
    "`num_domains` rows in every domain-aware layer and in the soft prompts "
    f"({src(f'{_XVLA}/soft_transformer.py', 226)} and line 344), and `num_domains` is 30 unless "
    f"config.json says otherwise ({src(f'{_XVLA}/configuration_xvla.py', 115)}), so a domain "
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
    f"({src(f'{_XVLA}/modeling_xvla.py', 69)}), and `ee6d` unless it says otherwise "
    f"({src(f'{_XVLA}/configuration_xvla.py', 122)}). `ee6d` and `agibot_ee6d` (line 220) are an "
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
    src(_GROOT_PROC, 1215),
    "the steps GR00T N1.7's processors are built from besides the default ones "
    "(`make_groot_pre_post_processors`, to line 1286): the step that packs the cameras, the "
    "state and the task (registered at line 1493), the one that encodes them with the "
    "backbone's own processor (line 2003), and one of two that decode the answer, the one for a "
    "checkpoint whose statistics come grouped by the actions they normalise (line 2263) and the "
    "one for a checkpoint that took its dataset's (line 2427), with the relative and absolute "
    "steps a checkpoint that learned relative actions with no grouped statistics adds (lines "
    "1244 and 1285). With the default ones, the only steps a GR00T may name",
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
    src(_GROOT_CFG, 283),
    "the GR00T a checkpoint is built from before its own weights replace it: `__post_init__` "
    "fills a missing one with `nvidia/GR00T-N1.7-3B` (lines 399 and 400), which the server "
    "refuses rather than let LeRobot load at no revision, as it refuses a SmolVLA naming no "
    "backbone. `GrootPolicy` builds `GR00TN17.from_pretrained(base_model_path)` "
    f"({src(f'{_GROOT}/modeling_groot.py', 111)}), which `snapshot_download`s the whole "
    f"repository at no revision unless it is a local directory ({src(_GROOT_N17, 928)}, to "
    "line 938) and loads its weights, and the checkpoint's own are loaded over them, strictly "
    "(modeling_groot.py lines 208 to 244). The server pins it, fetches its configs and "
    "safetensors, and hands the build that directory. The backbone is built from a config "
    "LeRobot carries and never fetched (line 923, and lines 359 to 363), but only while the "
    "base's config.json names `nvidia/Cosmos-Reason2-2B` as its `model_name`, or none, which "
    "means the same (line 85, and line 767): any other is fetched as an AutoConfig trusting its "
    "repository's code, at no revision (line 362), so the server reads the pinned base's "
    "config.json before any weight and refuses another backbone. It fetches the rest of the "
    "base at the commit that read found, since a tag asked again could name another commit by "
    "then, whose config.json nobody read",
)
GROOT_BACKBONE = "nvidia/Cosmos-Reason2-2B"
"""The backbone GROOT_BASE_MODEL says LeRobot builds from a config of its own."""
GROOT_VLM_PROCESSOR = UpstreamRef(
    "GrootN17VLMEncodeStep loads its tokenizer and processors with trust_remote_code=True",
    "VERIFIED",
    src(_GROOT_PROC, 1330),
    "`_build_n1_7_processor` loads `AutoTokenizer`, `Qwen2VLImageProcessor` and "
    "`Qwen3VLVideoProcessor` from the step's `model_name` with trust_remote_code=True, hard-wired "
    "(to line 1334), the first time the step runs (`proc`, line 2033, to line 2037), so on a "
    "session's first chunk and not when the server starts. `nvidia/Cosmos-Reason2-2B`, the one "
    "LeRobot's factory names (line 1220), is gated, and it is also what a step that names no "
    f"`model_name` is built with (line 2018, and {src(_GROOT_CFG, 48)}), as at 0.6.1, so such a "
    "step would load it at no revision, trusting its code. The server pins `model_name`, "
    "fetches its configs and tokenizer files and no `.py` and no weight, and refuses its "
    "directory if it holds anything else or maps a class to code (`auto_map`), so with "
    "trust_remote_code set there is no code of the repository's own for it to trust. It "
    "refuses a step that names no `model_name`, as it refuses a SmolVLA config that names no "
    "backbone, and one that keeps its model inside the checkpoint (PROCESSOR_ARTIFACTS). The "
    "first of those predates 0.7.0: quackd read this default at 0.6.1 and pinned only a "
    "`model_name` a step named, so a step that named none passed with no pin",
)
GROOT_DECODE = UpstreamRef(
    "groot_n1_7_action_decode_v1 unnormalises each action group and makes a relative one "
    "absolute against the state the pack step cached",
    "VERIFIED",
    src(_GROOT_PROC, 2263),
    "each group the checkpoint's `modality_config` names is unnormalised by its own statistics "
    "(lines 2326 to 2355), its `rep` and `type` read lowered (`config_value`, utils.py line 88), "
    "and a RELATIVE NON_EEF group has the raw state the pack step cached "
    "added back (lines 2357 to 2380), where an EEF one is a pose composed with xyz+rot6d "
    "(lines 2381 and 2382). With no `raw_stats` or `modality_config` it hands the action back "
    "still normalised (lines 2297 and 2298), and LIBERO's transform makes the gripper -1 or 1 "
    "(line 2244, to 2257), as `action_decode_transform` says, which `libero_sim` gets "
    f"unless it says otherwise ({src(_GROOT_CFG, 410)}, to line 413, and processor_groot.py line "
    "525). A checkpoint that took its dataset's statistics decodes through "
    "`groot_action_unpack_unnormalize_v2` instead, over its state file's `action` min and max "
    "(lines 2452 to 2473), still normalised with no statistics or `normalize_min_max` off, and "
    "with LIBERO's gripper -1 or 1 (lines 2475 to 2482). The server serves a GR00T whose every "
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
    src(_GROOT_PROC, 2309),
    "the decode step refuses to decode a relative checkpoint's actions one at a time (to line "
    f"2314), and `GrootPolicy.select_action` refuses a relative policy outright "
    f"({src(f'{_GROOT}/modeling_groot.py', 549)}, to line 555): it is predict_action_chunk and "
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
    f"({src(_GROOT_PROC, 1980)}), so no `observation.state.q01` is where NORMALIZER_STATS looks "
    "and the frame check would go unmade. The grouped statistics are the pack step's "
    "`raw_stats`, the state's and the absolute action's grouped as the actions are (lines "
    "993 to 1001), so the server reads the state's and the action's q01 and q99 from there, "
    "group by group in `modality_config`'s order, before any weight. A checkpoint that took its "
    "dataset's statistics keeps them in its state file, q01 and q99 among them where the "
    "dataset had them, and is read as every other type's is",
)
GROOT_N1_5_REMOVED = UpstreamRef(
    "LeRobot 0.7.0 serves GR00T N1.7 alone, and refuses N1.5's step names",
    "VERIFIED",
    src(_GROOT_PROC, 2546),
    "N1.5's processors name `groot_pack_inputs_v3`, `groot_eagle_encode_v3`, "
    "`groot_eagle_collate_v3` and `groot_action_unpack_unnormalize_v1` (to line 2551), which "
    "LeRobot registers only as stubs that fail with its removal guidance, and an N1.5 config "
    f"sets `tokenizer_assets_repo` ({src(_GROOT_CFG, 365)}, refused at line 392) or names an "
    "N1.5 base (`infer_groot_model_version`, lines 105 to 119), as at 0.6.1. The server refuses "
    "each in a sentence of its own, before a pin or a weight",
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
    src(_GROOT_PROC, 1524),
    "the pack step orders the views it hands the backbone by the checkpoint's "
    "`video_modality_keys` (to line 1582): a key no camera matches is dropped, so the model sees "
    "fewer views than it learned on, and with none matching it falls back to every camera in "
    "alphabetical order (lines 1548 to 1559). It pads no missing view, so the arm needs a camera "
    "for every image a GR00T looks at, and the server refuses a checkpoint whose modality keys "
    "name an image its config.json does not",
)
GROOT_INFERENCE_PACKAGES = UpstreamRef(
    "a GR00T N1.7 answers with diffusers and dm-tree beside what every family needs, and "
    "LeRobot's own [groot] extra adds the rest for training",
    "VERIFIED",
    src(_GROOT_N17, 472),
    "the action head is built only with diffusers installed (`require_package`, and again in "
    "`action_head/cross_attention_dit.py` lines 52 and 112), and every step's inputs go through "
    f"dm-tree (line 873). LeRobot's `[groot]` extra ({src('pyproject.toml', 242)}) adds peft, "
    "timm, decord and its `[dataset]` extra, which brings datasets, pyarrow, pandas, av and "
    "torchcodec, for training: its own note (line 246) says the dataset is built for "
    "relative-action training statistics, and processor_groot.py requires datasets only in "
    f"`_make_relative_action_training_stats_from_dataset_meta` ({src(_GROOT_PROC, 811)}, line "
    "820). So the server's extra brings diffusers and dm-tree, about 25 MB, rather than "
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
    src(f"{_XVLA}/processor_xvla.py", 426),
    "the post-processor step LIBERO's X-VLA is built with (`make_xvla_libero_pre_post_processors`, "
    "line 502, to line 522): it reads an end effector's position and rotation out of the action, "
    "turns the rotation into an axis and an angle and the gripper into -1 or 1 (to line 482), "
    "for LIBERO's simulator. No served type allows it, and the server refuses it in a sentence "
    "of its own",
)
XVLA_STRICT_LOAD = UpstreamRef(
    "XVLAPolicy.from_pretrained loads its weights strictly",
    "VERIFIED",
    src(f"{_XVLA}/modeling_xvla.py", 468),
    "whatever `strict` it is passed (line 480), it loads its state dict with strict=True (line "
    "548), once it has put back the tied embedding a save drops (lines 539 to 546), so the "
    "server's strict=True asks nothing more of it. Its Florence-2 is built from config.json's "
    f"`florence_config` ({src(f'{_XVLA}/configuration_xvla.py', 170)}, and line 87 here), never "
    "fetched, so the only model an X-VLA names is its tokenizer's, `facebook/bart-large` unless "
    "it says otherwise (configuration_xvla.py line 105)",
)
DEVICE_OVERRIDE = UpstreamRef(
    'overrides={"device_processor": {"device": device}}',
    "VERIFIED",
    src("src/lerobot/rollout/context.py", 631),
    "how upstream's own loops put a loaded pre-processor on the device they run on (and "
    f"{src('src/lerobot/scripts/lerobot_eval.py', 873)}): an override keyed by the step's "
    f"registry name, merged over its saved config ({src(_PIPELINE, 1259)}). The server puts "
    "the post-processor's device step on the CPU, so the actions it answers with are there",
)
DEVICE_STEP = "device_processor"
AUTO_DEVICE = UpstreamRef(
    "lerobot.utils.device_utils.auto_select_torch_device()",
    "VERIFIED",
    src(_DEVICE_UTILS, 23),
    "cuda, then mps, then xpu, then the CPU: the device the policy server loads a checkpoint "
    "on, and what it reports as having a GPU or not",
)
PROCESSOR_RESET = UpstreamRef(
    "DataProcessorPipeline.reset()",
    "VERIFIED",
    src(_PIPELINE, 1721),
    "resets every step that keeps state. The server resets the policy and both processors at "
    f"every session's reset, as upstream's sync inference does ({src(_SYNC, 85)}, to line 90), "
    "which 0.7.0 moved from `lerobot.rollout.inference` to `lerobot.inference`",
)
NORMALIZER_STATS = UpstreamRef(
    "NormalizerProcessorStep.state_dict() -> {'<feature>.<stat>': Tensor}",
    "VERIFIED",
    src(_NORMALIZE, 177),
    "the statistics a checkpoint was normalised with, flat, `observation.state.q01` and "
    f"`.q99` among them where the dataset had quantiles ({src(_NORMALIZE, 390)}). The server "
    "reports the state's and the action's 1st and 99th percentiles, which is how the arm's side "
    "tells whether the policy learned from an arm calibrated like this one",
)
QUANTILE_STATS = ("q01", "q99")
ACTION_FEATURE_NAMES = UpstreamRef(
    "PI05Config.action_feature_names, a list of names or None",
    "VERIFIED",
    src(_PI05_CFG, 74),
    "the names of the action's dimensions, filled from the dataset by make_policy "
    f"({src(_FACTORY, 344)}) on the policies that have the field, pi0's among them "
    f"({src(f'{_PI0}/configuration_pi0.py', 59)}). The server reports them where a checkpoint "
    "has them, and the arm's side checks them against its bus's motors",
)
MISSING_IMAGES_PADDED = UpstreamRef(
    "SmolVLA, pi05, pi0 and X-VLA run with some of their images missing",
    "VERIFIED",
    src(f"{_SMOLVLA}/modeling_smolvla.py", 391),
    "an image key with nothing under it is left out (SmolVLA pads up to `empty_cameras` of "
    "them, line 426), and only all of them missing raises (line 394, pi05's "
    f"{src(f'{_PI05}/modeling_pi05.py', 1375)}, and pi0's "
    f"{src(f'{_PI0}/modeling_pi0.py', 1013)}, which pads each missing one with -1 and a mask of "
    "zero, to line 1062). X-VLA stacks the images it is given and pads them out to "
    f"`num_image_views` with zeros and a mask of zero ({src(f'{_XVLA}/modeling_xvla.py', 351)}, "
    "to line 381, only all of them missing raising). ACT takes every image it names, and so "
    "does GR00T (GROOT_IMAGE_FALLBACK) "
    f"(`predict_action_chunk` stacks each, {src(_ACT_MODEL, 134)}), so the arm's side "
    "refuses an ACT with an image no camera is mapped to, and says which of the others' go "
    "padded",
)
ACT_BACKBONE_WEIGHTS = UpstreamRef(
    'ACTConfig.pretrained_backbone_weights = "ResNet18_Weights.IMAGENET1K_V1"',
    "VERIFIED",
    src(_ACT_CFG, 99),
    "torchvision fetches these ImageNet weights when ACT is built "
    f"({src(_ACT_MODEL, 337)}), before the checkpoint's own weights replace them. The server "
    "sets it to None, so building an ACT fetches nothing the checkpoint did not name",
)

RELATIVE_ACTIONS = UpstreamRef(
    "RelativeActionsProcessorStep caches the state the absolute step adds back, and holds it "
    "while a policy bound to it still plays the chunk predicted from it",
    "VERIFIED",
    src(_RELATIVE, 144),
    "training makes a chunk relative to one state, broadcast across it (`to_relative_actions`, "
    "line 44), and AbsoluteActionsProcessorStep (line 197) adds back the state the relative "
    "step cached (line 152). At 0.6.1 the step cached the state every time the pre-processor "
    "ran, and LeRobot's own loop ran the pre-processor, select_action and the post-processor "
    "every tick, so each action it played from its queue was made absolute against the state "
    "of the tick it was played at. At 0.7.0 the step holds its state while the policy it is "
    "bound to has actions queued (lines 148 to 152, `bind_relative_anchor` line 247), and "
    "LeRobot's rollout and eval bind it "
    f"({src('src/lerobot/rollout/context.py', 645)}, and lerobot_eval.py line 281), so its own "
    f"loop, still a pre-processor every tick ({src(_SYNC, 122)}), now makes a chunk absolute "
    "against the state it was predicted from. That is what the policy server does over a whole "
    "chunk at once, as OpenPI's AbsoluteActions does: it binds nothing and needs nothing bound, "
    "since it runs the pre-processor once a chunk, and a pi05 or a pi0 that learned relative "
    "actions is served in chunks, not asked every tick",
)
PI05_FROM_PRETRAINED = UpstreamRef(
    "PI05Policy.from_pretrained returns the model without its weights when they do not load",
    "VERIFIED",
    src(f"{_PI05}/modeling_pi05.py", 1102),
    "pi05 overrides the loader: a model.safetensors it cannot read returns the model as it was "
    "built (line 1181), and a load_state_dict that raises is caught and printed "
    "(line 1212), so a pi05 whose weights did not load comes back as a random network with only "
    "a printed line to say so. Changed at 0.7.0: a class that sets `use_native_pretrained_loader` "
    "takes LeRobot's own loader instead (lines 1118 to 1131), which pi05 does not (line 1070), "
    "and the key work moved into `_convert_openpi_state_dict` (line 1216): "
    "`_fix_pytorch_state_dict_keys` (line 1251), a `model.` prefix, and "
    "`_prepare_pretrained_state_dict` (line 1238, to line 1249), which fills "
    "`model.proprio_history_proj.weight` and `.bias` from the model as it was built when the "
    "config sets `use_proprioceptive_memory` and the checkpoint lacks them. The policy server "
    "loads a pi05's weights as that loader does, `_fix_pytorch_state_dict_keys` and a `model.` "
    "prefix, strictly and with nothing caught, and fills no projection: a checkpoint with "
    "proprioceptive memory that lacks those two weights is refused by its strict load, since a "
    "projection as it was built is a random layer, which is what the strict load exists to "
    "refuse. A pi05 with memory is refused on its config.json before that (PI05_MEMORY), and "
    "every load goes through the policy's own key rename (LEGACY_VISION_KEYS)",
)
PI05_MEMORY = UpstreamRef(
    "a pi05 with MEM keeps one observation each time it is asked, and looks back over them "
    "memory_stride apart",
    "VERIFIED",
    src(f"{_PI05}/modeling_pi05.py", 1454),
    "new at 0.7.0: with `use_visual_memory` or `use_proprioceptive_memory` set "
    f"({src(_PI05_CFG, 50)} and line 51), a pi05 keeps a ring of `(memory_frames - 1) * "
    "memory_stride + 1` observations (lines 1327 to 1341), adds one each time `select_action` "
    "(line 1500) or `predict_action_chunk` (line 1533) is asked with a single observation, and "
    "hands the model `memory_frames` of them `memory_stride` apart (lines 1477 to 1482), a "
    "stride its config counts in the dataset's frames (configuration_pi05.py lines 46 to 49). "
    "LeRobot's own loop asks it every tick. The server asks it once a chunk, so the history it "
    "saw would reach `n_action_steps` times as far back as the one it learned on, and a pi05 "
    "with either memory set is refused on its config.json, before its processors are read",
)
PI05_MEMORY_FLAGS = ("use_visual_memory", "use_proprioceptive_memory")
"""The config keys PI05_MEMORY says turn a pi05's memory on."""
PI0_FROM_PRETRAINED = UpstreamRef(
    "PI0Policy.from_pretrained returns the model without its weights when they do not load",
    "VERIFIED",
    src(f"{_PI0}/modeling_pi0.py", 802),
    "pi0's loader is pi05's: a model.safetensors it cannot read returns the model as it was "
    "built (lines 863 to 866), and a load_state_dict that raises is caught and printed (lines "
    "912 and 913), so a pi0 whose weights did not load comes back as a random network. It "
    "kept 0.6.1's shape at 0.7.0, with none of pi05's new loader switch, conversion or memory. "
    "The policy server loads a pi0's weights the way it loads a pi05's, with pi0's own "
    "`_fix_pytorch_state_dict_keys` (line 917) and a `model.` prefix, strictly and with nothing "
    "caught",
)
LEGACY_VISION_KEYS = UpstreamRef(
    "PreTrainedPolicy renames a state dict's vision_tower.vision_model. keys to vision_tower. as "
    "it loads one",
    "VERIFIED",
    src(_POLICY, 110),
    "new at 0.7.0, which moved to transformers 5.22 (LeRobot #5220, in its release notes): "
    "`__init__` registers `rename_legacy_vision_model_keys` as a `load_state_dict` pre-hook "
    f"({src(_UTILS, 102)}, to line 107), for checkpoints saved before transformers 5.6 "
    "flattened SigLIP's tower. It runs on every `load_state_dict` of a policy, so the server's "
    "own strict load of a pi05 or a pi0 (PI05_FROM_PRETRAINED), which calls the policy's "
    "`load_state_dict`, loads such a checkpoint's vision tower as LeRobot's own loaders do, and "
    "a weight the rename leaves that the policy lacks is still refused "
    "(`tests/test_policy_pipeline.py`)",
)
PI0_FAST_REMOTE_CODE = UpstreamRef(
    "PI0FastPolicy loads the FAST tokenizer with trust_remote_code=True",
    "VERIFIED",
    src(f"{_PI0_FAST}/modeling_pi0_fast.py", 771),
    "`PI0FastPolicy.__init__` loads `AutoProcessor.from_pretrained(config.action_tokenizer_name, "
    "trust_remote_code=True)` (to line 773), hard-wired in the policy rather than in a "
    "processor step an override could reach, so the FAST tokenizer's repository runs its own "
    "Python wherever a pi0_fast is built. `action_tokenizer_name` is "
    "`lerobot/fast-action-tokenizer` unless a config says otherwise "
    f"({src(f'{_PI0_FAST}/configuration_pi0_fast.py', 65)}), and the type registers as "
    "`pi0_fast` at line 30. The server refuses the type before it reads anything past "
    "config.json",
)

# ── LeRobot's own policy servers, and why quackd has one of its own ─────────────────────
# 0.6.1's `lerobot.async_inference` is gone at 0.7.0, which has `lerobot.remote_inference` in
# its place: each row says what the first did, and cites what the second does at the pin.

ASYNC_PICKLE = UpstreamRef(
    "LeRobot 0.6.1's async inference unpickled what it was sent, and 0.7.0's remote inference, "
    "which replaced it, decodes bounded MessagePack",
    "VERIFIED",
    src(_CODEC, 353),
    "0.6.1's policy server `pickle.loads` every observation and the policy spec, and its robot "
    "client every chunk it got back, over gRPC's `add_insecure_port` "
    "(`async_inference/policy_server.py` and `robot_client.py`, read at v0.6.1), so anything "
    "that could reach the port ran code on the other side. 0.7.0 removed "
    "`lerobot.async_inference` and its `[async]` extra, and `lerobot.remote_inference`, over "
    f"zenoh and msgpack (`[remote]`, {src('pyproject.toml', 291)}), took its place. Its server "
    f"and its client decode every message with this codec's `decode_message` (line 398; "
    f"{src(f'{_REMOTE}/server.py', 896)} and line 922, and remote_inference/client.py line "
    "163), which unpacks MessagePack with every extension refused (line 359, "
    "`_reject_extension` line 316) and every length bounded (lines 360 to 364), and its README "
    f"says it decodes 'without pickle' ({src(f'{_REMOTE}/README.md', 329)}). quackd has read "
    "no more of it than those lines. quackd's protocol is JSON with every number checked, "
    "behind a token, and quackd runs neither of LeRobot's servers",
)
ASYNC_CLIENT_NEEDS_TORCH = UpstreamRef(
    "LeRobot 0.6.1's async robot client imported torch, and 0.7.0's remote client does too",
    "VERIFIED",
    src(f"{_REMOTE}/client.py", 29),
    "and the process that owns the serial bus is the one quackd keeps free of torch. 0.6.1's "
    "`async_inference/robot_client.py` imported it (read at v0.6.1); 0.7.0 removed that module, "
    "and `RemoteClient`, which replaced it, imports torch (line 29), as the codec it decodes "
    f"with does ({src(f'{_REMOTE}/codec.py', 19)}), though it imports no policy class (line 15)",
)
ASYNC_SKIPS_SIMILAR = UpstreamRef(
    "LeRobot 0.6.1's async server skipped an observation near the last one, and 0.7.0's remote "
    "inference takes the newest observation each time it asks for a chunk",
    "VERIFIED",
    src(f"{_REMOTE}/engine.py", 528),
    "0.6.1's server ran `observations_similar(obs1, obs2, lerobot_features, atol=1)` and "
    "skipped an observation whose joint state was within a norm of 1 of the last one it ran "
    "(`async_inference/helpers.py`, read at v0.6.1), which is how a policy stopped seeing an "
    "arm that was barely moving. 0.7.0 removed it. Its remote engine keeps the newest "
    "observation in one slot (line 528), and its worker takes it from there (line 686) when "
    "its chunk runtime says a chunk is due (line 759); quackd found nothing in "
    "`lerobot.remote_inference` that compares one observation with another, and read no more "
    "of it than that. quackd's server runs every step it is asked",
)
ASYNC_SUPPORTED_POLICIES = UpstreamRef(
    "LeRobot 0.6.1's async server took SUPPORTED_POLICIES = "
    '["act", "smolvla", "diffusion", "tdmpc", "vqbet", "pi0", "pi05", "groot"], and 0.7.0\'s '
    "remote server takes any type it can build",
    "VERIFIED",
    src(f"{_REMOTE}/deployment.py", 146),
    "0.6.1's list (`async_inference/constants.py`, read at v0.6.1) refused any other type, an "
    "X-VLA among them. 0.7.0 removed it with the module, and its remote server builds whatever "
    "type a checkpoint's config.json names (`load_deployment`, line 117, by `get_policy_class` "
    "here), with that type's own `from_pretrained` at its default strictness (line 156) and the "
    "processors as the checkpoint saved them (line 158), checked against no list of its own. "
    "quackd's server takes the types it read (POLICY_TYPES), through allowlists of its own",
)

POLICY_PIPELINE = UpstreamRef(
    "POLICY_PIPELINE: build_inference_frame, the pre-processor, predict_action_chunk, the "
    "post-processor over the chunk, make_robot_action a row at a time",
    "VERIFIED",
    src(_FACTORY, 158),
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
    "ones: nothing about what a trained policy does to an arm follows from it. The job runs "
    "LeRobot 0.7.0 with torch 2.15 on the CPU, transformers 5.22.2 and huggingface-hub 2.4.0, "
    "what `lerobot[xvla]` resolved to on 2026-11-08: it brings no diffusers, whose 0.39.0 holds "
    "the hub below 2 in quackd's own lock, at 1.33.0",
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
    "against the state it was predicted from. That last one is how training made the chunk "
    "relative, and since 0.7.0 what LeRobot's own loop does too (RELATIVE_ACTIONS), so a "
    "trained pi05 or pi0 is taken to want it. That GR00T N1.7 runs through it too, with its "
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
    src(_POLICY, 406),
    "that an ACT with temporal_ensemble_coeff set, asked through select_action every tick and "
    "its one action put through the post-processor, answers what upstream's own loop would "
    "play. The server refuses one without a GPU, because it has to answer inside a tick, so "
    "CI's CPU job cannot run it, and no bench has",
)
LOAD_POLICY = UpstreamRef(
    "LOAD_POLICY",
    "UNVERIFIED",
    src(_UTILS, 158),
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
