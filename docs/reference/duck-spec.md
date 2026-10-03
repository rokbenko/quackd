# The `.duck` file — spec v0, v1, v2 and v3 (normative)

A `.duck` file is a task for an LLM-piloted robot, or for a flock of them. It is
deliberately **SKILL.md-shaped**: YAML frontmatter between `---` fences, then a Markdown
body. The frontmatter is a contract the executor enforces; the body is the prompt. **The LLM
is never trusted to self-police.**
`.duck` is the format name the way `Dockerfile` is: a task for a LeRobot arm is a `.duck`
too. `duck: 1` (quackd 0.4) adds what a multi-robot task needs, and `duck: 2` adds what a
task says about the *body*: a correction to the robot's own datasheet, and what a flock role
physically needs ([ADR-0019](adr/0019-duck-spec-v1.md),
[ADR-0032](adr/0032-datasheets-and-the-verdict.md)). `duck: 3` adds what a task lets the
body's learned policy be told, and for how long ([`policy`](#policy-v3)). `duck: 0` files parse
and run unchanged.

Machine-readable schema: [`../quackd/duckfile/schema.json`](../quackd/duckfile/schema.json)
(generated from `quackd/duckfile/schema.py`; a test keeps them in sync).

## File shape

```
# optional comment lines above the first fence are allowed
---
<YAML mapping>
---
<Markdown body — must be non-empty>
```

Encoding UTF-8. The first non-blank, non-comment line must be `---`.

## Frontmatter fields

| Field | Type | Required | Enforced by | Meaning |
|---|---|---|---|---|
| `duck` | `0`, `1`, `2` or `3` | yes | parser | Spec version. `1` unlocks `requires`, `robots`, `flock.roles`, `flock.frame_hints` and `flock.allocation.method: pilots`; `2` unlocks `datasheet` and `flock.roles.<role>.needs`; `3` unlocks `policy`. Using a key under too low a version is an error that names the fix. |
| `name` | slug `^[a-z0-9][a-z0-9-]{0,63}$` | yes | parser | Identifier; run directories and the fake pilot's strategies key on it. |
| `description` | string | yes | — | One human-facing line. Shown in the system prompt. |
| `author` | string | no | — | Credit. |
| `verbs.allow` | list of verb names, ≥ 1, unique | yes | **executor** | The only verbs the LLM may call. `stop` is always allowed. Unknown names fail `quackd validate`. |
| `verbs.confirm` | list ⊆ `allow` | no (default `[]`) | **executor** | Verbs that prompt a human y/N before running (`--yes` auto-accepts; MCP refuses unless `--yes`; a pipe on stdin opens the gate as it always has, and only an answer a person really gave is recorded as one, [safety.md](safety.md#who-the-record-says-was-asked)). |
| `budgets.max_steps` | int 1–1000 (default 40) | no | **executor** | Maximum verb executions. |
| `budgets.max_minutes` | number > 0 ≤ 180 (default 5) | no | **loop** | Robot-clock cap (sim time on every simulator, `sim2d` and both bodies' `mujoco`, wall-clock on hardware). Checked before each model call and again the moment the model answers, so a provider that replies late cannot spend the overrun. A verb already running is not interrupted, so a run can overshoot by that verb's own timeout. |
| `budgets.max_llm_calls` | int 1–2000 (default 40) | no | **loop** | Maximum provider calls (re-prompts count). |
| `success` | list of strings, ≥ 1 | yes | LLM (+ ground truth in sim tests) | Criteria the model judges itself against via `declare_success(reason)`. |
| `abort_when` | list of strings | no | **executor** for two phrasings; LLM otherwise | See below. |
| `persona` | string | no | — | Tone. Inserted verbatim into the system prompt. |
| `providers` | list of strings | no | — | Tested-with, **not** a restriction. |
| `learned_verbs` | list of `{name, policy, description?, metadata?}` | no | `validate` rejects non-empty | Reserved for v2 ([learned-verbs.md](learned-verbs.md)). |
| `flock` | mapping, see below | no | **coordinator** or **pilots** | Cooperating robots. Absent means a single robot, unless the run names a stored flock (`--flock NAME`), which makes it a pilot flock. |
| `requires` | list of verb names ⊆ `allow` (v1) | no (default `[]`) | `validate --robot` | The verbs the task *needs*. Checked against each robot's manifest. For a v0 file every allowed verb is required. |
| `datasheet` (v2) | mapping, see below | no | loop and MCP session | Corrections and additions to the robot's own datasheet, for the build in front of you. Rendered in the prompt as coming from the task file. |
| `robots` | `<adapter>[:<backend>]`, or a mapping member → spec (v1) | no | CLI | The default robot(s), so `quackd run <duck>` needs no `--robot`. Flags win over the file. |
| `policy` (v3) | mapping, see below | no | **executor** and the robot | The instructions `manipulate` may give the robot's learned policy, how long each segment runs and how long they may run in all ([`policy`](#policy-v3)). Needs `manipulate` in `verbs.allow`. |

### `requires` and `robots` (v1)

`requires` is the honest minimum: a robot that lacks one of these verbs cannot do the task,
and `quackd validate <duck> --robot <adapter>:<backend>` says so with a field-level line
such as `requires kick, but arm-01 (lerobot-so101) does not provide it` (exit 1). Verbs in
`allow` that are not required are advisory: a robot may lack them and still qualify, and
`validate` reports them as a weaker `verbs.allow` line. For a solo task every listed robot
must provide every required verb; for a flock, the flock as a whole must (see the roles
below). Aliases count: a robot that provides `observe` satisfies `get_frame`.

`robots` names the default robot for a solo task (`robots: microduck:sim2d`) or one per
flock member (`robots: {duck-01: microduck:sim2d, duck-02: microduck:sim2d}`).

### `flock` — cooperating robots

`allocation.method` chooses the kind. **`auction`** (the default) is the 0.3 coordinator:
2 to 4 Microducks in one `sim2d` arena on a lockstep clock, members are state machines, at
most one model call for the whole run, and every other key in this block is read. An auction
duck with a non-empty `verbs.confirm` fails `quackd validate`, because an auction member has
no pilot and no terminal to prompt on. **`pilots`** (`duck: 1`, 0.9) is the other kind: 2 to 8
bodies on any backend, one LLM pilot each, on wall-clock time, splitting the work by talking.
A pilot flock reads only `members`, takes `verbs.confirm` with `--yes`, and names no roles.
Full semantics: [flock.md](flock.md).

| Field | Type | Default | Enforced by | Meaning |
|---|---|---|---|---|
| `flock.members` | int, or a list of unique slugs. 2–4 for an auction, 2–8 for pilots | 3 | coordinator or pilots | Member count (named `duck-0`…) or explicit names. `--flock N` overrides the count for an auction; `--flock NAME` supplies the members from the registry. |
| `flock.allocation.method` | `auction` · `pilots` (v1) | `auction` | parser | Which kind of flock. `auction`: Contract Net, one referee, sim2d Microducks. `pilots`: one LLM per body, any backend, and every other `allocation`, `safety`, `search` and `roles` key below is ignored ([ADR-0034](adr/0034-registered-robots-and-pilot-flocks.md)). |
| `flock.allocation.bid` | `ball_distance` | `ball_distance` | coordinator | Lower camera-estimated distance wins. |
| `flock.allocation.tie_break` | `duck_id` | `duck_id` | coordinator | Lexicographic member name. |
| `flock.allocation.hysteresis_pct` | 0–100 | 20 | coordinator | A challenger must bid this much lower to unseat the current claimant. |
| `flock.allocation.claim_lease_s` | > 0 ≤ 60 | 6 | coordinator | Longest a claim may be held before re-auction (sim clock). A fixed fuse from the grant, not a progress timer. |
| `flock.safety.min_separation_m` | 0.1–2.0 | 0.4 | coordinator | Non-kickers keep at least this far from the action. |
| `flock.safety.one_claimant` | bool | true | coordinator | At most one robot approaches the ball. Always enforced, `false` is rejected at validation. |
| `flock.safety.per_duck_heartbeat_s` | > 0 ≤ 10 | 1.0 | coordinator | Bus heartbeat period; the watchdog presumes a duck dead after 3× this, or this plus 2.5 s, whichever is longer. |
| `flock.search.partition` | `heading` | `heading` | coordinator | Each duck owns a heading sector. |
| `flock.search.restart_s` | > 0 ≤ 120 | 8 | member | Re-scan the sector when nothing was found for this long. |
| `flock.roles` (v1) | mapping `{spotter: {requires: [...]}, kicker: {requires: [...]}}` | absent | coordinator | Heterogeneous roles, **auction only**. A robot bids only for a role whose `requires` its manifest satisfies. quackd knows exactly these two roles (both must be given), one robot each; `members` must then be a named list. Each role's `requires` ⊆ `allow`. |
| `flock.roles.<role>.needs` (v2) | mapping in the datasheet vocabulary | `{}` | coordinator | What the body must be able to do, not only what it must know. See the rules below. Checked by `validate --robots`, by the member before it bids, and by the coordinator from what the bid carried ([flock.md](flock.md)). |
| `flock.frame_hints` (v1) | `auto` · `on` · `off` | `auto` | runner | Share arena-frame target hints between robots. `auto` is on only when every member runs in `sim2d`; there is no shared frame on hardware ([flock.md](flock.md)). |

Unknown keys anywhere are errors (`extra="forbid"`).

### `needs` — the datasheet vocabulary (v2)

A role's `needs` is checked against a robot's datasheet, and each key is checked its own way.
The same vocabulary is what a pilot names in `assess_task`, so a refusal and a role are
worded alike ([manifest-spec.md](manifest-spec.md#the-datasheet)).

| Key | How it is checked |
|---|---|
| `payload_kg`, `reach_m`, `arms` | minimums. The body must publish at least this much. |
| `endurance_min` | a minimum, except on a mains-powered body (`tethered: true`), which has nothing to run down and passes. |
| `work_height_m` | **not** a minimum: a height the hands must be able to reach, so it must fall inside the body's `workspace_height_m` band. Asking for 0.4 m fails a body that reaches 0.5 to 1.25 m, because that is below it. |
| `manipulator`, `mobility` | must match the body's own word. `any` accepts any word but `none`, so it means some kind: a body that does not move fails `mobility: any`. `none` asks for nothing: a task that goes nowhere names `mobility: none`, and every body meets it, one with legs or wheels too. |
| `terrain` | a floor, not a match: `indoor_flat` < `indoor` < `outdoor`, and a body rated for more than the task asks passes. A body that publishes no terrain meets `indoor_flat` and nothing above it, because that is what the prompt tells such a body to assume about itself. A body that does not move meets `indoor_flat` too, and anything above it fails as `(it does not move)`, which is what its prompt says where a moving body's names a terrain. A body with no datasheet at all meets none of them. |

Any number given as `0`, and `none` for `manipulator` or `mobility`, asks for nothing and is
always met. That includes `work_height_m`: `0` means the task does not turn on a height, not
that the hands work on the floor, and a task that does work at floor level says so with a
small height above zero. The pilot is told the same in the `assess_task` schema: name only
what the task turns on, and leave a field out, or give `0` or `none`, when it does not.

**A figure the maker never published counts as not met**, because a robot that cannot say
what it carries is not the one to ask to carry something. There are four exceptions, and all
of them exist so that an honest answer is not refused: a zero, a `none`, the floor a body with
no published terrain is already told to assume, and the flat indoor floor a body that does not
move stands on. The words are `manipulator: none · beak · gripper · arms · any`,
`mobility: none · legged · wheeled · any` and `terrain: indoor_flat · indoor · outdoor`. Of
those, `terrain` is the one to be careful with: five shipped bodies are rated `indoor_flat`,
the SO-101 does not move, and the rosbridge body publishes nothing, so a role asking for
`indoor` or `outdoor` can be filled by no robot quackd ships today
([manifest-spec.md](manifest-spec.md#the-datasheet)).

A role is read strictly on one point where a pilot's own verdict is not. A pilot judging its
own body is not refused for a `work_height_m` its sheet publishes no band for, because its
prompt never lists a working height as missing and it had no way to know. A role, the
coordinator judging a bid and the list of other bodies that could do a task all refuse it,
because a body that never said how high it works is not the one to offer a task at a height.

### `abort_when` — what is enforced

Two phrasings are recognised (case-insensitive) and enforced by the executor:

- `Battery below N%` (also `under`, `<`) — before every verb, if the robot reports
  `battery_percent < N`, the run aborts. A body whose manifest has no `battery` sensor
  reports `None` and this rule can never fire on it, so it is silently unenforceable
  there rather than an error. An AlohaMini is the shipped example
  ([adapters/alohamini.md](adapters/alohamini.md)).
- `Same verb fails N times in a row` — N consecutive failed results of one verb abort the run.

Every other entry is handed to the LLM under *"Abort conditions you must respect yourself"*.
The spec says this plainly rather than pretending prose is policy.

### Verb names

Anything the robot's manifest provides (`quackd list-verbs`, or `list-verbs --robot`):
the core verbs `observe report_state stop say move go_to search_scan approach_and` on any
robot that meets their requirements, a robot's own extensions (Microduck: `sit stand
stand_up kick grab gaze quack`), plus any registered learned verb. The 0.3 names
`get_frame`, `walk_to` and `walk` are permanent aliases of `observe`, `go_to` and `move`;
a file may use either spelling but not both. `stop` may never appear in `confirm`. Params
and ranges come from the registry, not the `.duck` file ([ADR-0018](adr/0018-core-verbs-extensions-aliases.md)).

## Body

Free Markdown, non-empty, placed verbatim at the end of the system prompt under
*"Task file: `<name>` — `<description>`"*. Conventions the starters follow:

- `# Task` — one or two sentences of intent.
- `## Strategy` — a numbered plan naming verbs in backticks.
- `## Notes` — failure modes and what to do about them (verify-and-retry, when to give up).

The body cannot widen the contract: a verb mentioned in the body but absent from `allow`
is refused at runtime and the LLM is told so.

## Runtime semantics

- The loop ends with one of `success`, `failure` (the LLM's declaration), `infeasible` (the
  pilot judged the task beyond this body, so nothing moved and `quackd run` exits 3),
  `budget`, `aborted` (heartbeat, kill switch, enforced `abort_when`), or `error` (a provider
  or transport that failed, or a bug). The robot is stopped in every case and its adapter
  closed.
- `--max-steps` on the CLI overrides `budgets.max_steps` for one run.
- `--dry-run` executes the verbs their adapter declared `read_only` (`observe`, alias
  `get_frame`, `report_state`, the rosbridge base's `introspect`) and logs everything else
  without sending an intent.

## Validation

`quackd validate <files or globs or bundled names>` prints a table and exits 1 on any
failure, with a path and a field-level reason. Checks: parse, schema (a `policy` section's
bounds, `manipulate` allowed beside it, no `pick` beside a list of instructions, and no
`policy` in a flock duck among them), unknown verbs, `learned_verbs` empty, no `confirm` in
an auction flock. With `--robot <adapter>:<backend>` (one or
more) or `--robots name=spec,...`, the file is also checked against those robots' manifests:
`requires` (or, for v0, `allow`) per robot, and every flock role fillable by at least one
robot. Without a flag, the duck's own `robots:` default is used, and a file that names no
robot is checked against every body installed here, with what each offers a policy server:
a task is coherent when something here can keep it.

## Resolution

`quackd run x` tries `x` as a path, then `x` / `x.duck` among the bundled starters
(`ducks/` in a checkout, `quackd/ducks/` inside the wheel).

## Versioning

`duck: 0` is the 0.1 to 0.3 contract ([ADR-0005](adr/0005-duck-spec-v0.md)); `duck: 1`
adds `requires`, `robots`, `flock.roles`, `flock.frame_hints` and, since 0.9,
`flock.allocation.method: pilots` ([ADR-0019](adr/0019-duck-spec-v1.md),
[ADR-0034](adr/0034-registered-robots-and-pilot-flocks.md)); `duck: 2` adds `datasheet` and
`flock.roles.<role>.needs` ([ADR-0032](adr/0032-datasheets-and-the-verdict.md)); `duck: 3`
(0.16) adds `policy`. Older files keep parsing because the version is explicit and the parser
is strict; the only new rejections a v0 file can hit are two contradictions no shipped file
contains (a verb listed next to its alias, `stop` in `confirm`). Older quackd versions refuse
newer files, which is the correct failure.

### `datasheet` (v2)

Every robot publishes what it weighs, can carry and can reach, each number with how sure
quackd is of it and who says so ([manifest-spec.md](manifest-spec.md)). A task file can
correct that for the build in front of it: a printed gripper that holds 300 g rather than
the 500 g a vendor lists, a reach somebody measured with a tape.

```yaml
duck: 2
datasheet:
  payload_kg: {value: 0.3, confidence: measured, source: weighed with the printed gripper}
  reach_m: 0.35
  cannot: [lift anything wider than the printed gripper's 60 mm opening]
```

A figure given here replaces the robot's own and is rendered as coming from the task file,
so the pilot can see which numbers are the maker's and which are yours. A bare number is
shorthand for `{value: n}`, and its confidence defaults to `estimate`. The words can be
corrected too, not only the figures: `manipulator`, `arms`, `tethered` and `terrain`. The
sentence lists (`cannot`, `notes`, `not_rated`) **extend** the robot's: a task file can add
something a body cannot do, and can never delete one. A sentence must start with a word and
stay under 300 characters, so the leading `-`, `` ` `` or `*` that a list invites is refused
at parse time: the prompt bullets these itself, and a backtick is how it spells a verb. A correction the body contradicts, a
payload on a robot with nothing to hold with, is refused by `validate` before the run starts. A flock
duck cannot carry one, because it describes one body.

### `policy` (v3)

`manipulate` hands an arm to its learned policy for one segment, told one short subtask in
the words the policy was trained on ([adapters/lerobot.md](adapters/lerobot.md)). A v3 task
file says which words those may be and how long the policy may drive:

| Field | Type | Default | Enforced by | Meaning |
|---|---|---|---|---|
| `policy.instructions` | list of unique one-line strings, at most 12, each at most 200 characters | `[]` | **executor** | The only instructions `manipulate` takes, word for word. They become an enum in the verb's own schema, so the pilot is shown exactly these, the executor refuses any other words, and a decision LLM can be offered each one. Empty lets the pilot word each subtask itself, held to the same one line and the same length, as the target the pilot gives `pick` is. A list refuses `pick` in `verbs.allow`, because `pick` tells the policy a target of the pilot's own. |
| `policy.segment_s` | number > 0, at most 60 | 10 | robot and **executor** | How long one segment runs in the robot's time, unless it ends sooner. The robot is told it before the first segment, and the executor's timeout for `manipulate` is this plus 10 s, plus, on the simulator, the wall time its clock stands still while the policy thinks, at most ten minutes of it. |
| `policy.total_s` | number > 0, at most 3600, and at least `segment_s` | 120 | **executor** | The seconds of segments the run may spend in all, `pick`'s as well as `manipulate`'s, each charged the seconds its verb said it ran, or the robot's clock across the call when it said none, a call cancelled or aborted mid-segment among them. A segment the robot refused before it began says it ran 0 s. One segment runs at a time, and a `pick` or `manipulate` sent while one runs is refused. Checked before each segment, so the last may run past it by at most its own length, and once it is spent the next `pick` or `manipulate` is refused as a budget. |

`manipulate` must be in `verbs.allow`, and a flock duck cannot carry a `policy`, because it
hands one arm to its policy. Each segment is still one step against `budgets.max_steps`. A run
whose task has none of this, a `--goal` run, a v2 file, or an MCP session with no task
loaded, gets the defaults above and any instruction. A second task file loaded over MCP is
held to its own section, narrowed from the arm's own verb rather than from the first file's
list, and the seconds of segments the session has run still count, those it ran before any
task file was loaded among them. A decision LLM is offered each listed
instruction and never takes one, whatever `--decision-mode` says: its answer is recorded beside
the pilot's, and the pilot starts every segment ([decision-llms.md](decision-llms.md)).

```yaml
---
duck: 3
name: stack-blocks
description: Stack the red block on the blue one with the arm's learned policy
verbs:
  allow: [report_state, manipulate, stop]
  confirm: [manipulate]
requires: [manipulate]
budgets:
  max_steps: 12
policy:
  instructions:
    - pick up the red block
    - place it on the blue block
    - open the gripper
  segment_s: 8
  total_s: 48
success:
  - The red block rests on top of the blue block, seen from the camera.
abort_when:
  - Same verb fails 3 times in a row
---
# Task
Stack the red block on the blue one.

## Strategy
1. Find both blocks in the camera frame your observation brings. Without a frame,
   `report_state` reads the arm, which cannot show where a block is.
2. `manipulate` with `pick up the red block`, then judge from the frame the next observation
   brings whether the arm holds it.
3. `manipulate` with `place it on the blue block`, then `open the gripper`.
4. Judge the stack from the frame after the last segment before you declare anything.
```

The file allows no `observe`. `quackd run` checks a task file against the arm as it describes
itself before it connects, which is without one, and refuses a file that allows it. The look
comes from the frame every observation brings the pilot on an arm with a camera.

A plain `quackd validate` checks a v3 file with no policy server running: the arm offers
`manipulate` to one, so the file is coherent wherever the arm is installed. `--robot NAME`
checks it against that body as it is registered, and a registration holds no policy server.
So on `lerobot:real` or `lerobot:mujoco`, or a robot registered as either, it refuses
`manipulate`, and says to start a server with `quackd policy serve` and give `quackd run` its
address with `--policy-url`. `--robot lerobot:mock` checks the file against an arm that has a
policy of its own. `quackd preflight` and `quackd run` with `--policy-url` check it against the
arm itself.

```console
$ quackd validate stack-blocks.duck
quackd validate
+----------------------------------------------------+
| file              | name         | verbs | result  |
|-------------------+--------------+-------+---------|
| stack-blocks.duck | stack-blocks |     3 | + valid |
+----------------------------------------------------+
+ 1 file valid
```

`quackd run stack-blocks.duck --controller vla --policy-url ...` hands the policy these three
instructions in order and then asks you whether the arm stacked the block
([adapters/lerobot.md](adapters/lerobot.md#a-scripted-pilot-that-a-person-judges---controller-vla)).
