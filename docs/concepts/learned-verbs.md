# Learned verbs (v2) — this is the shape; nothing here runs yet

**Status: reserved extension point.** The registry interface exists and is tested with a
dummy. Roboglot runs no ONNX policy of its own, and nothing routes a learned verb to one: a
Microduck's policies run on the robot, under `robotd`. Training still wants a GPU.

> [!NOTE]
> **The arm's learned policies are not learned verbs.** `pick` and `manipulate` hand the SO-101
> to a LeRobot policy, an ACT, a SmolVLA or a pi05, served by `roboglot policy serve` in a process
> of its own. Both are the arm's own verbs, declared in its manifest with parameters of their
> own, a target and an instruction, and neither goes through `register_learned_verb`, which
> registers a verb with no parameters into a registry the connect rebuilds from the manifest.
> [guides/policies.md](../guides/policies.md) is how to run one, and
> [ADR-0048](../adr/0048-policies-are-the-arms-executor.md) is why they are shaped this way. This
> page is about something else: skills trained from rewards an LLM wrote, registered as one
> more verb.

## The idea

Today every verb is either a shipped robot behaviour or Python over shipped behaviours.
v2 adds a third kind: a **learned verb** = an ONNX policy plus metadata that registers as
one more verb, so the LLM can call `moonwalk` exactly like it calls `kick`. The LLM's job
(pick a verb) does not change; the vocabulary grows.

The training story is [Eureka](https://eureka-research.github.io/) /
[DrEureka](https://eureka-research.github.io/dr-eureka/)-style: an LLM writes a reward
function for a described skill, [`microduck_rl`](https://github.com/pollen-robotics/microduck_rl)
(mjlab / MuJoCo Warp + PPO) trains it, the export produces an ONNX policy, and Roboglot
registers it. Roboglot's part is the last step.

## The shape today

```python
from roboglot.verbs.learned import LearnedVerbSpec, register_learned_verb
from roboglot.verbs.registry import default_registry

spec = LearnedVerbSpec(
    name="moonwalk",
    description="Walk backwards smoothly for two seconds.",
    policy_path="policies/moonwalk.onnx",  # obs[1,61] -> actions[1,14], 50 Hz (upstream's contract)
    timeout_s=5.0,
    metadata={"reward": "eureka:v3", "trained_on": "mjlab 2026-08"},
)
verb = register_learned_verb(default_registry(), spec, runner=None)
```

`default_registry()` is the Microduck manifest's registry, which matters: a verb
that is not in a robot's manifest does not exist for that robot, so registering a learned
verb into a registry is only half the story. For it to be offered to the LLM, allowed by a
`.glot`, or listed by MCP on a given body, that body's manifest has to declare it. When
learned verbs ship for real, the spec becomes a `VerbSpec` in the owning adapter's manifest
([adapters/writing-an-adapter.md](../adapters/writing-an-adapter.md),
[reference/manifest-spec.md](../reference/manifest-spec.md)); until then treat the call above as a
registry-level sketch, not a supported path on an arbitrary robot. The nearest thing that ships is a
different shape on purpose: `pick` and `manipulate` on the LeRobot arm are each one skill intent the
arm's own learned policy executes, declared by the arm's adapter rather than registered here
([adapters/lerobot/README.md](../adapters/lerobot/README.md#manipulate-and-the-loop-a-policy-runs-in)).

- `safety_class` is always `confirm`: an unproven policy asks a human first.
- `runner` is `async (spec, ctx) -> VerbResult`. Without one the verb explains that it is a
  v2 feature and fails cleanly (tested).
- A `.glot` can already *declare* them under `learned_verbs:` (parsed, validated, and
  rejected by `roboglot validate` in v0.1 so nobody ships a file that silently does nothing).

## What upstream's policies look like

Every shipped policy is `obs[1,61] → actions[1,14]` at 50 Hz: 48 proprioception values +
a 13-value command `[vel(3), head(4), body(6)]`; the observation normaliser is baked into
the ONNX at export. The whole contract is upstream's own, read at commit `bae39f5` of
`microduck_rl`: the observation, the command and the control loop in
[`scripts/infer_policy.py`](https://github.com/pollen-robotics/microduck_rl/blob/bae39f5900b318f7e87fc485c30a31213cfa14eb/scripts/infer_policy.py),
and the metadata written into the ONNX in
[`export.py`](https://github.com/pollen-robotics/microduck_rl/blob/bae39f5900b318f7e87fc485c30a31213cfa14eb/src/mjlab_microduck/export.py). Upstream's
`manifest.json` lists every policy with its kind (perpetual, scripted or episodic) and its
command encoding. `robotd` checks the shape at load and points at policies by slot in
`robotd.toml` (`[policy] walk = ...`). At daemon-v0.18.8 the slots are `walk`, `stand`,
`sitstand`, `ground_pick`, `kick_left`, `kick_right` and `roulade`, and roller mode puts the
roller and its crouch in `walk` and `ground_pick`. The default `walk` is `velstand.onnx`, one
network that walks on a twist and stands still at zero command, so `stand` is empty unless a
board loads `alpha_stand.onnx` back by hand.

## What has to exist before a runner is real

1. **A way to run a policy on the robot on demand.** `robotd` has had one since API v18:
   `robot.loadPolicy` puts another `.onnx` in one slot while the robot runs, swapping it at the
   home pose seconds later, and since v22 `robot.setSkill` makes a policy answer to `robot.do`
   by name, with how long it runs and the twist it unwinds on. Both write `robotd.toml`, so
   what a verb loads outlives the verb, a reboot included, until it is put back
   (`robot.loadPolicy` with no path, or `robot.removeSkill`). A path in either is a file on the
   robot, so a new policy has to be there first: `policy.fetch`, which `updaterd` answers rather
   than `robotd`, puts one from the Hub in the robot's library. Roboglot calls none of them.
   A runner reaches the robot through `ctx.transport`, which `VerbContext` already carries, so
   nothing is needed on the executor side.
2. **An answer to the episodic problem.** The policy a Microduck walks and stands on by default,
   `velstand.onnx`, is perpetual: it is handed a twist every tick and never finishes. A learned
   verb is the other kind. The entry pose, the start condition and the stop condition are not
   in the 61-value observation. Upstream's manifest carries some of them beside it, an episodic
   policy's `duration_s` and, since `v5`, an `entry_pose`, so far only velstand's, and Roboglot
   has not worked out what a runner does with them. Until that is answered, the cheapest first
   learned verb to attempt is a perpetual one with the same contract: a different gait rather
   than a new skill.
3. **Provenance.** `metadata` should carry the reward text, the training run, and the
   eval numbers, so a `.glot` author knows what they are allowing. Upstream's
   `manifest.json` is the shape to copy: one entry per policy with its kind and its command
   encoding, and since `v5` some entries also say where they came from, as `training`: the
   task, the repository and commit, the run and the checkpoint.

## How to help

- Prototype a runner's plumbing against `microduck:mock`, which shows the registry, the confirm
  gate and the executor working and runs no policy, and keep
  `tests/test_registry.py::test_learned_verb_registers_and_runs` green. A policy itself runs on
  the robot, or in `microduck_rl`'s evaluation env, which wants Python 3.12 exactly, torch, warp
  and mjlab.
- If you are upstream: the policy slot over the socket this page asked for has existed since
  API v18, as `robot.loadPolicy`, and Roboglot will read it into `upstream_api.py` before
  anything here calls it.
