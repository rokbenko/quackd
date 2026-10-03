# Learned verbs (v2) — this is the shape; nothing here runs yet

**Status: reserved extension point.** The registry interface exists and is tested with a
dummy. quackd does run ONNX policies now, but only the two the Microduck ships and only
through `microduck:mujoco`; nothing routes a learned verb to one. Training still wants a GPU.
Running one no longer wants a duck.

> [!NOTE]
> **The arm's learned policies are not learned verbs.** `pick` and `manipulate` hand the SO-101
> to a LeRobot policy, an ACT, a SmolVLA or a pi05, served by `quackd policy serve` in a process
> of its own. Both are the arm's own verbs, declared in its manifest with parameters of their
> own, a target and an instruction, and neither goes through `register_learned_verb`, which
> registers a verb with no parameters into a registry the connect rebuilds from the manifest.
> [policies.md](policies.md) is how to run one, and
> [ADR-0048](adr/0048-policies-are-the-arms-executor.md) is why they are shaped this way. This
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
(mjlab / MuJoCo Warp + PPO) trains it, the export produces an ONNX policy, and quackd
registers it. quackd's part is the last step.

## The shape today

```python
from quackd.verbs.learned import LearnedVerbSpec, register_learned_verb
from quackd.verbs.registry import default_registry

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
verb into a registry is only half the story. For it to be offered to the model, allowed by a
`.duck`, or listed by MCP on a given body, that body's manifest has to declare it. When
learned verbs ship for real, the spec becomes a `VerbSpec` in the owning adapter's manifest
([adapters.md](adapters.md), [manifest-spec.md](manifest-spec.md)); until then treat the call
above as a registry-level sketch, not a supported path on an arbitrary robot. The nearest
thing that ships is a different shape on purpose: `pick` and `manipulate` on the LeRobot arm
are each one skill intent the arm's own learned policy executes, declared by the arm's adapter
rather than registered here ([adapters/lerobot.md](adapters/lerobot.md#manipulate-and-the-loop-a-policy-runs-in)).

- `safety_class` is always `confirm`: an unproven policy asks a human first.
- `runner` is `async (spec, ctx) -> VerbResult`. Without one the verb explains that it is a
  v2 feature and fails cleanly (tested).
- A `.duck` can already *declare* them under `learned_verbs:` (parsed, validated, and
  rejected by `quackd validate` in v0.1 so nobody ships a file that silently does nothing).

## What upstream's policies look like

Every shipped policy is `obs[1,61] → actions[1,14]` at 50 Hz: 48 proprioception values +
a 13-value command `[vel(3), head(4), body(6)]`; the observation normaliser is baked into
the ONNX at export. The whole contract is cited at a pin in
[`adapters/microduck/src/quackd_microduck/sim3d/upstream_api.py`](../adapters/microduck/src/quackd_microduck/sim3d/upstream_api.py) (`OBSERVATION`, `COMMAND`,
`CONTROL`, `POLICY_METADATA`), which is what the MuJoCo backend reads. Upstream's
`manifest.json` lists every policy with its kind (perpetual, scripted or episodic) and its
command encoding. `robotd` checks the shape at load and points at policies by role in
`robotd.toml` (`[policy] walk = ...`, read 2026-08-28). Roles today: walking, standing,
sit↔stand, ground pick, kick left/right, roller, roller crouch, roulade.

## What has to exist before a runner is real

1. **A way to run a policy on the robot on demand.** Today `robotd` loads a fixed set of
   roles from config at startup. A learned verb needs either an upstream "policy slot" that
   can be hot-swapped over the socket (not designed yet), or a `robotd.toml` edit + restart
   (slow, but real). Track upstream; do not guess.
2. **A sim runner.** This is the part that changed. `microduck:mujoco` runs upstream's
   walking and standing policies on upstream's model at 50 Hz on a laptop
   ([ADR-0030](adr/0030-mujoco-physics-backend.md)), so the machinery a learned verb needs
   already exists in `adapters/microduck/src/quackd_microduck/sim3d/microduck.py`: an onnxruntime session, the 61-value
   observation built from the model's own state, and `ctrl = default_pose + action *
   action_scale` written every tick. What is missing is a way in. `MicroduckBody` loads
   exactly two sessions in `__init__` and chooses between them in `control()` by the twist
   norm. There is no third slot, no way to hand it a policy for the length of one verb, and
   no termination condition for a policy that ends. A runner reaches the world through
   `ctx.transport`, which `VerbContext` already carries, so nothing is needed on the
   executor side. A sim runner for `sim2d` is still out of scope: the cartoon has no joints.
3. **An answer to the episodic problem.** Both policies quackd runs are perpetual: they are
   handed a twist every tick and never finish. A learned verb is the other kind, and
   upstream's own episodic policies (`ball_kick_left`, `ball_kick_right`,
   `alpha_ground_pick`, `alpha_sitstand`) did nothing from a standing pose when quackd tried
   them, which is why `kick`, `grab` and `sit` on `microduck:mujoco` are stand-ins rather
   than policies (`KICK_STANDIN` in
   [`adapters/microduck/src/quackd_microduck/sim3d/upstream_api.py`](../adapters/microduck/src/quackd_microduck/sim3d/upstream_api.py)). The entry pose, the
   start condition and the stop condition are not in the 61-value observation, and nobody
   has worked out where they belong. Until that is answered, the cheapest first learned verb
   to attempt in sim is a perpetual one with the same contract: a different gait rather than
   a new skill.
4. **Provenance.** `metadata` should carry the reward text, the training run, and the
   eval numbers, so a `.duck` author knows what they are allowing. Upstream's
   `manifest.json` is the shape to copy: one entry per policy with its kind and its command
   encoding.

## How to help

- Prototype a runner against `microduck:mujoco`, which needs `quackd[mujoco]` and no GPU,
  rather than against `microduck_rl`'s evaluation env, which wants Python 3.12 exactly, torch,
  warp and mjlab. Keep `tests/test_registry.py::test_learned_verb_registers_and_runs` green.
- If you are upstream: a hot-swappable policy slot over the socket is the one API this
  needs. We will track it in `upstream_api.py` the day it is designed.
