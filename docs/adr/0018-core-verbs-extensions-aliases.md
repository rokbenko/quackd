# ADR-0018: Core verbs, extensions, and one alias table

**Status:** accepted, amended · **Date:** 2026-09-02 · Implemented in Phase 1 of 0.4 ([design](../design/multi-robot.md))

**Amended 2026-09-28 by [ADR-0048](0048-policies-are-the-arms-executor.md):** LeRobot's extensions below gain `manipulate`, which
hands the arm to its learned policy for one short subtask. The last consequence still holds, a
verb reaches a robot only through its manifest and adapter, and a task file adds none. But a
v3 task file now narrows one verb's parameters to its own list, `manipulate`'s instruction, so
the schema a pilot is shown is the adapter's verb as the task narrowed it. And the core treats
`pick` and `manipulate` by name as the verbs that hand a body to a learned policy: their seconds
are budgeted, one runs at a time, and a list of instructions refuses `pick` beside it.

## Context

The 0.3 vocabulary is duck-shaped: `walk`, `walk_to`, `get_frame`, `quack`. A stationary
head cannot walk and an arm cannot quack, yet the composite verbs (`search_scan`,
`approach_and`) and the safety layer should work on any body. Renaming verbs is cheap in
code and expensive for users: every shipped and community `.duck` spells the old names,
the fake pilot emits them, and `tests/test_docs.py` requires every registry name to be
backticked in the README.

## Decision

- **Core verbs** exist on any robot whose manifest satisfies their requirement:
  `observe` (camera), `report_state` (nothing), `stop` (nothing), `say(text)` (intent
  `sound`), `move(twist)` (intent `twist` and mobility not `none`), `go_to(target)`
  (observe plus move), `search_scan(target)` (observe plus move or gaze), `approach_and`
  (go_to). Requirements live in `REQUIREMENTS` in `quackd/verbs/core.py` and are validated
  on the manifest itself, so a manifest that declares `go_to` on a robot with
  `mobility: none` is rejected before any registry or run exists.
- **Extensions** are declared per adapter: Microduck `sit stand stand_up kick grab gaze
  quack`, LeRobot `pick place move_joints gripper`. `quack` and `say` are separate verbs
  (different parameter models on the same intent), not aliases.
- **Composite verbs choose their strategy from the manifest.** `search_scan` turns in
  place when the robot has twist and mobility (exactly today's loop, also for bare
  transports) and sweeps the head when it only has gaze. An adapter may override that
  choice for its own body, because the rule is about what the manifest allows rather than
  what is wise: the ToddlerBot supplies its own `search_scan` that always sweeps the neck,
  since turning a fall-prone humanoid on the spot to look around is not something to do
  without a get-up policy (ADR-0028). The gaze sweep starts from the
  current head yaw, alternates outward (`c, c+s, c-s, c+2s, ...`) within
  `limits["gaze_yaw_deg"]`, leaves the head on the target, and reports `gaze_yaw_deg`
  because bearings stay camera-relative on every robot.
- **Renames with permanent aliases:** `get_frame` to `observe`, `walk_to` to `go_to`,
  `walk` to `move`. All aliases live in exactly one file, `quackd/verbs/aliases.py`, and
  nothing else in quackd may spell an alias mapping.
- **Registries store canonical names only.** `get()`, `__contains__()` and `unknown()`
  resolve aliases; `view(name)` returns the verb *as the caller named it*, so tool schemas
  and the system prompt show the spelling the `.duck` used. `Executor.allowed` returns the
  contract's list verbatim and `is_allowed` compares canonically. A `.duck` that lists both
  `walk` and `move` is a schema error. Transcript `verb` events keep `name` as called and
  gain `canonical`, so replays and the flock one-claimant check key on a stable name.
- **The bundled starter ducks stay on the old spellings** (they are also still `duck: 0`),
  so `uvx quackd run find-and-kick --provider fake`, the fake pilot, the hello-world golden
  transcript and the tool names cloud LLMs see are unchanged in 0.4.

## Consequences

- `default_registry().names()` is canonical (`observe report_state stop say move go_to
  search_scan approach_and sit stand stand_up kick grab gaze quack`); the README backticks
  the new names next to the old ones.
- A manifest never lists aliases; discovery and MCP advertise canonical names, and any
  spelling in a `.duck` still validates.
- `approach_and`'s nested result key becomes `go_to` (was `walk_to`); no test read it.
- The only way to add a verb to a robot is through its manifest and adapter, which is what
  keeps "a verb not in the manifest does not exist" true.
