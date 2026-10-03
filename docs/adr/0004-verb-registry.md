# ADR-0004: Everything the LLM can do is a verb in one registry

**Status:** accepted, amended · **Date:** 2026-08-28 · Extended by [ADR-0018](0018-core-verbs-extensions-aliases.md) (0.4: the one registry is built from a robot's manifest; `verbs/builtin.py` and `verbs/composite.py` became `verbs/core.py` plus per-adapter verb modules)

**Amended 2026-09-28 by [ADR-0048](0048-policies-are-the-arms-executor.md):** a learned policy on the arm is not a learned verb.
`pick` and `manipulate` hand the SO-101 to a LeRobot policy, and both are the arm's own verbs
(`quackd_lerobot.verbs`), declared in its manifest like every other extension and with
parameters of their own, a target and an instruction. `register_learned_verb` stays the
reserved extension point it is below: a verb with no parameters, shaped for an ONNX policy the
Microduck runs, and registered into a registry the connect rebuilds from the manifest, so it
could never have told a policy a subtask. The fourth list the Context names, learned ONNX
policies, is still that point and nothing more ([learned-verbs.md](../concepts/learned-verbs.md)).

## Context

Four things need the same list of actions: the LLM's tool definitions, the `.duck`
allowlist, the MCP tool list, and — in v2 — learned ONNX policies. Four lists drift; one
does not.

## Decision

A `Verb` (`quackd/verbs/registry.py`) is data plus one coroutine:

```
name · description (LLM-facing) · params: pydantic model · execute(ctx, params) -> VerbResult
timeout_s · safety_class: safe|confirm|dangerous · preconditions · done_condition · kind
```

- **Built-in verbs** (`verbs/builtin.py`) map 1:1 to shipped behaviours: `walk`, `sit`,
  `stand`, `kick`, `grab`, `stand_up`, `stop`, `quack`, `gaze`, `get_frame`.
- **Composite verbs** (`verbs/composite.py`) are plain Python over built-ins + perception:
  `search_scan`, `walk_to`, `approach_and`. They call other verbs through `ctx.run_verb`, so
  the executor's allowlist and budgets still apply inside a composite.
- **Learned verbs** (`verbs/learned.py`) are a reserved extension point: `LearnedVerbSpec`
  (ONNX path + metadata) + `register_learned_verb()`. Interface, dummy test and docs only.
- `Verb.tool_schema()` emits the provider-neutral `{name, description, input_schema}` shape
  (Anthropic's), which every provider translates. `additionalProperties: false` always.
- Execution policy (allowlist, confirm, budget, dry-run) lives in `quackd/safety.py`, never
  in a verb. A verb that raises is caught, the duck is stopped, and the LLM is told.

## Consequences

- Adding a verb is one function + one `registry.register(...)` — CONTRIBUTING documents it.
- `quackd list-verbs`, the MCP server and the system prompt are all views of the registry.
- Param validation errors are returned to the LLM as a failed `VerbResult`, not raised: a
  wrong argument is feedback, not a crash.
