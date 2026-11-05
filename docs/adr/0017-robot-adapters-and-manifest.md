# ADR-0017: Robots are adapters that declare a manifest

**Status:** accepted, amended · **Date:** 2026-09-02 · Implemented in Phase 1 of 0.4 ([design](../design/multi-robot.md))

**Amended 2026-11-05 by [ADR-0075](0075-an-adapter-is-a-family-a-body-file-and-a-binding.md):**
"adding a robot means one package" holds for a robot of a new kind on a new route. A robot of a
kind a package already drives, or one that speaks a protocol a package already speaks, is a body
record in that package, data the package loads, plus a thin distribution that keeps its name;
and a robot nobody here can run is one YAML in the core
([ADR-0077](0077-a-body-quackd-only-describes-is-one-yaml-in-the-core.md)). The manifest is still
what decides which verbs exist, and `describe()` still returns it without an SDK; what changed is
that a body's manifest is loaded from its record rather than built by code of its own.

**Amended 2026-09-28 by [ADR-0048](0048-policies-are-the-arms-executor.md):** two things the decision below takes as given have
changed. A verb's parameter schema no longer comes from its adapter alone. A `duck: 3` task
file's `policy` section narrows `manipulate`'s `instruction` to the instructions it lists, as an
inline enum, and its timeout to the task's own segment (`quackd/duckfile/narrow.py`), rebuilt
each time from the verb the adapter registered. So a task file, which is untrusted input,
narrows one verb and never widens one or adds one. And the core now treats two verb names as
its own: the executor charges `pick` and `manipulate` against the task's seconds of policy and
lets one segment run at a time, by name, on any body that registers either. The consequence
that adding a robot needs nothing in the executor, the loop or the prompts still holds for every
robot without those two verbs.

## Context

Through 0.3 quackd knew exactly one body: the Microduck, addressed by `--transport`. The
verb registry was hardcoded (`default_registry()` registered builtins plus composites), the
executor's vocabulary was therefore the Microduck's, and the MCP server, `quackd validate`
and the system prompt were all views of that one list. Physical robot hardware exists
today; Microduck hardware does not. Generalizing means the robot must tell quackd what it
is and what it can do, and quackd must build everything else from that.

## Decision

- **`RobotManifest`** (`quackd/adapters/manifest.py`) is a pydantic model, `extra="forbid"`,
  JSON on the wire (MCP, mDNS, the bus), never YAML on disk. It declares identity
  (`id`, `vendor`, `model`), body (`embodiment`, `mobility`), channels (`intents`,
  `sensors`), the verb list with `core` flags and safety classes, `preconditions` as
  verb-to-condition-name references, `safety_authority`, `frame`, `limits`, and a free
  `extras` map. `manifest.intents` keeps the brief's vocabulary
  (`twist skill gaze sound joint pose gripper`) and one table, `INTENT_KIND_FOR`, maps it
  onto the code's existing `Intent.kind` values (`move do look sound joint pose gripper`).
- **The verb registry is built from the manifest at connect time**
  (`registry_from_manifest`). The manifest decides *which* verbs exist and how they are
  gated; code decides *how* they run (implementations are looked up by canonical name in
  the adapter first, then in `verbs/core.py`). A verb that is not in the connected robot's
  manifest does not exist: not in the registry, not in the MCP tool list, not in `.duck`
  validation, not in the system prompt.
- **`RobotAdapter`** (`quackd/adapters/base.py`) is a Protocol that is a superset of
  `DuckTransport`: `connect() -> RobotManifest`, `disconnect()`/`close()`, `get_state()`,
  `get_frame()`, `send_intent()`, `health()`, `heartbeat()`, `stop()`, `subscribe()`,
  `now()`, `sleep()`, `preconditions()` (named predicates the manifest references) and
  `implementations()` (extension verbs and core overrides). Because it is a superset, an
  adapter is a drop-in wherever 0.3 took a transport.
- **`heartbeat()` stays the watchdog contract** (raise `HeartbeatError`, `safety.Heartbeat`
  stops and aborts). `health()` is the informational call for doctor, `robot_list` and
  discovery. Two methods, one meaning each; the brief's "health" is split this way because
  the watchdog semantics are pinned by tests and by the deadman argument in ADR-0012.
- **Preconditions move from the verb definitions into the adapter.** The executor never
  hardcoded "not fallen, not sitting"; `verbs/builtin.py` did. Now the manifest says
  `preconditions: {move: [standing], sit: [not_fallen]}` and the adapter supplies the
  predicates by name, so the manifest stays JSON while the predicates stay code.
- **The four Microduck transports are wrapped, not moved.** `quackd/transport/*` is
  untouched and becomes the Microduck backend layer; `MicroduckAdapter(transport)`
  delegates every call and returns `microduck_manifest(backend)`. This is the only
  mechanical proof of "zero behaviour change" for the Microduck path, and it leaves the
  UNVERIFIED containment test of ADR-0006 and every `quackd.transport.*` import exactly as
  they are.
- **Every adapter also exposes a static `describe(backend, robot_id)`** that returns the
  manifest without importing an SDK or opening a socket, so `quackd validate`,
  `list-verbs --robot` and `quackd announce` work offline; `connect()` returns the same
  object, possibly enriched (for example a live `express` enum).
- **Selection syntax** is `--robot <adapter>[:<backend>]` on every command and
  `--robots name=<adapter>:<backend>,...` for flocks and multi-robot MCP. `--transport X`
  is a deprecated alias of `--robot microduck:X` for exactly one release (one stderr line
  per process, a `DeprecationWarning`), removed in 0.5. A `.duck` may declare default
  robots (`robots:`) so `quackd run <duck>` works without flags.
- **`stop` is universal**: inserted into every manifest if absent, forced
  `safety_class="safe"`, always allowed, never confirm-gated.

## Consequences

- The default install still runs the Microduck simulator unchanged; `find-and-kick` is
  10 of 10 on seeds 0 to 9 before and after, with the same intents and the same transcript.
- Adding a robot means one package under `quackd/adapters/` with a manifest, a double, a
  doctor row and a docs page, and nothing in the executor, the loop or the prompts.
- Manifests must be honest: the Microduck over `jsonrpc` without `--camera-url` still
  declares a camera in 0.4 (the robot has one; the link does not carry it yet), so a run
  fails at `observe` exactly as 0.3 did rather than at validate. Revisit when the camera
  path exists.
- `DuckState` keeps its name (no rename of duck branding); non-duck robots leave posture
  `unknown` and use `holding` for grippers.
