# The `.glot` file — spec v0, v1, v2, v3 and v4 (normative)

A `.glot` file is a task for an LLM-piloted robot, or for a team of them. It is
deliberately **SKILL.md-shaped**: YAML frontmatter between `---` fences, then a Markdown
body. The frontmatter is a contract the executor enforces; the body is the prompt. **The LLM
is never trusted to self-police.**
`.glot` is the format name the way `Dockerfile` is: a task for a LeRobot arm is a `.glot`
too. `glot: 1` (Roboglot 0.4) adds what a multi-robot task needs, and `glot: 2` adds what a
task says about the *body*: a correction to the robot's own datasheet
([ADR-0019](../adr/0019-glot-spec-v1.md), [ADR-0032](../adr/0032-datasheets-and-the-verdict.md)).
`glot: 3` adds what a task lets the body's learned policy be told, and for how long
([`policy`](#policy-v3)). `glot: 4` adds one thing a task says about the pilot rather than the
body: that it may be given a part to play ([`play`](#play-v4)). `glot: 0` files parse and run
unchanged, but for a `team:` block ([Versioning](#versioning)).

Machine-readable schema: [`../roboglot/glotfile/schema.json`](../../roboglot/glotfile/schema.json)
(generated from `roboglot/glotfile/schema.py`; a test keeps them in sync).

## File shape

```
# optional comment lines above the first fence are allowed
---
<YAML mapping>
---
<Markdown body — must be non-empty>
```

Encoding UTF-8, and a byte-order mark at the very start is skipped. A file in any other encoding,
UTF-16 among them, is refused with an error that says to save it as UTF-8. The first non-blank,
non-comment line must be `---`.

## Frontmatter fields

| Field | Type | Required | Enforced by | Meaning |
|---|---|---|---|---|
| `glot` | `0`, `1`, `2`, `3` or `4` | yes | parser | Spec version. `1` unlocks `requires`, `robots` and `team`, `2` unlocks `datasheet`, `3` unlocks `policy` and `4` unlocks `play`. Using a key under too low a version is an error that names the fix. |
| `name` | slug `^[a-z0-9][a-z0-9-]{0,63}$` | yes | parser | Identifier; run directories and the fake pilot's strategies key on it. |
| `description` | string | yes | — | One human-facing line. Shown in the system prompt. |
| `author` | string | no | — | Credit. |
| `verbs.allow` | list of verb names, ≥ 1, unique | yes | **executor** | The only verbs the LLM may call. `stop` is always allowed. Unknown names fail `roboglot validate`. |
| `verbs.confirm` | list ⊆ `allow` | no (default `[]`) | **executor** | Verbs that prompt a human y/N before running (`--yes` auto-accepts; MCP refuses unless `--yes`; a pipe on stdin opens the gate as it always has, and only an answer a person really gave is recorded as one, [concepts/safety.md](../concepts/safety.md#who-the-record-says-was-asked)). |
| `budgets.max_steps` | int 1–1000 (default 40) | no | **executor** | Maximum verb executions. |
| `budgets.max_minutes` | number > 0 ≤ 180 (default 5) | no | **loop** | Robot-clock cap (a mock's own clock on a mock, wall-clock on hardware). Checked before each LLM call and again the moment the LLM answers, so a provider that replies late cannot spend the overrun. A verb that watches for it ends at its next tick once the minutes are spent, as `move`, `go_to`, `watch` and the arm's `move_joints` do. One that does not runs to its own end, so a run can overshoot by that verb's own timeout. |
| `budgets.max_llm_calls` | int 1–2000 (default 40) | no | **loop** | Maximum provider calls (re-prompts count). A turn the LLM's API could not answer, and Roboglot asked again, counts once ([ADR-0059](../adr/0059-a-turn-the-api-could-not-answer-is-asked-again.md)). The verb the last allowed call chooses runs, and the run ends on this budget when it would make one call more. |
| `success` | list of strings, ≥ 1 | yes | LLM | Criteria the LLM judges itself against via `declare_success(reason)`. |
| `abort_when` | list of strings | no | **executor** for two phrasings; LLM otherwise | See below. |
| `persona` | string | no | — | Tone. Inserted verbatim into the system prompt. |
| `play` (v4) | bool | no (default `false`) | — | The task may give the pilot a part to play, an animal, a person or a character, which it acts out with the body it has. Adds one section, `## Playing a part`, to the system prompt and changes no gate. `roboglot run --play` or `--no-play` overrides it for one run ([`play`](#play-v4), [guides/play.md](../guides/play.md)). |
| `providers` | list of strings | no | — | Tested-with, **not** a restriction. |
| `learned_verbs` | list of `{name, policy, description?, metadata?}` | no | `validate` rejects non-empty | Reserved for v2 ([concepts/learned-verbs.md](../concepts/learned-verbs.md)). |
| `team` | mapping, see below (v1) | no | **pilots** | Cooperating robots, one LLM pilot each. Absent means a single robot, unless the run names a stored team (`--team NAME`), which makes it a team all the same. |
| `requires` | list of verb names ⊆ `allow` (v1) | no (default `[]`) | `validate --robot` | The verbs the task *needs*. Checked against each robot's manifest. For a v0 file every allowed verb is required. |
| `datasheet` (v2) | mapping, see below | no | loop and MCP session | Corrections and additions to the robot's own datasheet, for the build in front of you. Rendered in the prompt as coming from the task file. |
| `robots` | `<adapter>[:<backend>]`, or a mapping member → spec (v1) | no | CLI | The robot the task runs on, or one per team member, so `roboglot run <task>` needs no `--robot`. Flags win over the file. |
| `policy` (v3) | mapping, see below | no | **executor** and the robot | The instructions `manipulate` may give the robot's learned policy, how long each segment runs and how long they may run in all ([`policy`](#policy-v3)). Needs `manipulate` in `verbs.allow`. |

### `requires` and `robots` (v1)

`requires` is the honest minimum: a robot that lacks one of these verbs cannot do the task,
and `roboglot validate <task> --robot <adapter>:<backend>` says so with a field-level line
such as `requires kick, but arm-01 (lerobot-so101) does not provide it` (exit 1). Verbs in
`allow` that are not required are advisory: a robot may lack them and still qualify, and
`validate` reports them as a weaker `verbs.allow` line. For a solo task every listed robot
must provide every required verb, and for a team, the team as a whole must. Aliases count: a
robot that provides `observe` satisfies `get_frame`.

`robots` names the robot for a solo task (`robots: microduck:mock`) or one per team member
(`robots: {duck: microduck:mock, arm: lerobot:mock}`, as the bundled `team-hello` has it).

### `team` — cooperating robots

A team (`glot: 1`) is 2 to 8 bodies on any backend, one LLM pilot each, on wall-clock time,
splitting the work by talking. It reads only `members` and takes `verbs.confirm` with `--yes`.
Full semantics: [guides/team.md](../guides/team.md).

| Field | Type | Default | Enforced by | Meaning |
|---|---|---|---|---|
| `team.members` | int, or a list of unique slugs, 2 to 8 | 3 | pilots | Member count (named `robot-0`…) or explicit names. `--team NAME` supplies the members from the registry. |
| `team.allocation.method` | `pilots` | `pilots` | parser | Which kind of team, and `pilots` is the only kind: one LLM per body, any backend ([ADR-0034](../adr/0034-registered-robots-and-pilot-teams.md)). |

Unknown keys anywhere are errors (`extra="forbid"`).

### `needs` — the datasheet vocabulary (v2)

A pilot's verdict names, in `assess_task`, what the task would need of a body, and each key of
that `needs` is checked against the robot's datasheet its own way. A `feasible` verdict is held
to the body's own sheet by these rules, the same rules say which other body could do a task,
and a `glot: 2` task file can correct the sheet they read ([`datasheet`](#datasheet-v2),
[reference/manifest-spec.md](manifest-spec.md#the-datasheet)).

| Key | How it is checked |
|---|---|
| `payload_kg`, `reach_m`, `arms` | minimums. The body must publish at least this much. |
| `carries_kg` | a minimum too: what rides on the body's back or deck as it moves. It needs no hand, so a body with no manipulator can meet it, and one that publishes no such figure does not. |
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
those, `terrain` is the one to be careful with: seven shipped bodies are rated `indoor_flat` and
the other three publish no terrain, so a task needing `indoor` or `outdoor` can be done by no
robot Roboglot ships today ([reference/manifest-spec.md](manifest-spec.md#the-datasheet)).

The list of other bodies that could do a task is read strictly on one point where a pilot's own
verdict is not. A pilot judging its own body is not refused for a `work_height_m` its sheet
publishes no band for, because its prompt never lists a working height as missing and it had no
way to know. The list refuses it, because a body that never said how high it works is not the
one to offer a task at a height.

### `abort_when` — what is enforced

Two phrasings are recognised (case-insensitive) and enforced by the executor:

- `Battery below N%` (also `under`, `<`) — before every verb, if the robot reports
  `battery_percent < N`, the run aborts. A body whose manifest has no `battery` sensor
  reports `None` and this rule can never fire on it, so it is silently unenforceable
  there rather than an error. An AlohaMini is the shipped example
  ([adapters/alohamini/README.md](../adapters/alohamini/README.md)).
- `Same verb fails N times in a row` — N consecutive failed results of one verb abort the run.
  A call refused for its arguments is one of them, so a pilot that sends the same speed past the
  clamp N times in a row ends the run on this rule.

Every other entry is handed to the LLM under *"Abort conditions you must respect yourself"*.
The spec says this plainly rather than pretending prose is policy.

### Verb names

Anything the robot's manifest provides (`roboglot list-verbs`, or `list-verbs --robot`):
the core verbs `observe report_state stop say move go_to search_scan approach_and map mark_place
forget_place go_to_place` on any robot that meets their requirements, a robot's own extensions
(Microduck: `sit stand stand_up kick grab gaze quack`, and `watch` on a run started with
`--decision-frames`), plus any registered learned verb. The 0.3 names `get_frame`, `walk_to`
and `walk` are permanent aliases of `observe`, `go_to` and `move`; a file may use either
spelling but not both. `stop` may never appear in `confirm`. Params and ranges come from the
registry, not the `.glot` file ([ADR-0018](../adr/0018-core-verbs-extensions-aliases.md)).

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
  pilot judged the task beyond this body, so nothing moved and `roboglot run` exits 3),
  `budget`, `aborted` (heartbeat, kill switch, enforced `abort_when`), or `error` (a provider
  or transport that failed, or a bug). The robot is stopped in every case and its adapter
  closed.
- `--max-steps` on the CLI overrides `budgets.max_steps` for one run.
- `--play` and `--no-play` override `play` for one run, on `run`.
- `--dry-run` executes the verbs their adapter declared `read_only` (`observe`, alias
  `get_frame`, `report_state`, the rosbridge base's `introspect`) and logs everything else
  without sending an intent.

## Validation

`roboglot validate <files or globs or bundled names>` prints a table and exits 1 on any
failure, with a path and a field-level reason. Checks: parse, schema (a `policy` section's
bounds, `manipulate` allowed beside it, no `pick` beside a list of instructions, and no
`policy` in a team task among them), unknown verbs and `learned_verbs` empty. With
`--robot <adapter>:<backend>` (one or more) or `--robots name=spec,...`, the file is also
checked against those robots' manifests: `requires` (or, for v0, `allow`) by each robot for a
solo task, and by at least one robot for a team. Without a flag, the task file's own `robots:`
default is used, and a file that names no robot is checked against every body installed here,
with what each offers a policy server: a task is coherent when something here can keep it.

## Resolution

`roboglot run x` tries `x` as a path, then `x` / `x.glot` among the bundled starters
(`tasks/` in a checkout, `roboglot/tasks/` inside the wheel).

## Versioning

`glot: 0` is the 0.1 to 0.3 contract ([ADR-0005](../adr/0005-glot-spec-v0.md)). `glot: 1`
adds `requires`, `robots` and `team` ([ADR-0019](../adr/0019-glot-spec-v1.md),
[ADR-0034](../adr/0034-registered-robots-and-pilot-teams.md)), `glot: 2` adds `datasheet`
([ADR-0032](../adr/0032-datasheets-and-the-verdict.md)), `glot: 3` (0.16) adds `policy` and
`glot: 4` (0.19) adds `play` ([ADR-0055](../adr/0055-a-pilot-can-play-a-part.md)). Older files
keep parsing because the version is explicit and the parser is strict, with two exceptions. A
v0 file can hit two contradictions no shipped file contains, a verb listed next to its alias and
`stop` in `confirm`. And a `team:` block in a `glot: 0` file, or one with any key but `members`
and `allocation.method: pilots`, is refused with the key named, because the kind of team it was
written for is gone. Older Roboglot versions refuse newer files, which is the correct failure.

### `datasheet` (v2)

Every robot publishes what it weighs, can carry and can reach, each number with how sure
Roboglot is of it and who says so ([reference/manifest-spec.md](manifest-spec.md)). A task file can
correct that for the build in front of it: a printed gripper that holds 300 g rather than
the 500 g a vendor lists, a reach somebody measured with a tape.

```yaml
glot: 2
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
payload on a robot with nothing to hold with, is refused by `validate` before the run starts. A team
task cannot carry one, because it describes one body.

### `policy` (v3)

`manipulate` hands an arm to its learned policy for one segment, told one short subtask in
the words the policy was trained on ([adapters/lerobot/README.md](../adapters/lerobot/README.md)). A
v3 task file says which words those may be and how long the policy may drive:

| Field | Type | Default | Enforced by | Meaning |
|---|---|---|---|---|
| `policy.instructions` | list of unique one-line strings, at most 12, each at most 200 characters | `[]` | **executor** | The only instructions `manipulate` takes, word for word. They become an enum in the verb's own schema, so the pilot is shown exactly these, the executor refuses any other words, and a decision LLM can be offered each one. Empty lets the pilot word each subtask itself, held to the same one line and the same length, as the target the pilot gives `pick` is. A list refuses `pick` in `verbs.allow`, because `pick` tells the policy a target of the pilot's own. |
| `policy.segment_s` | number > 0, at most 60 | 10 | robot and **executor** | How long one segment runs in the robot's time, unless it ends sooner. The robot is told it before the first segment, and the executor's timeout for `manipulate` is this plus 10 s. |
| `policy.total_s` | number > 0, at most 3600, and at least `segment_s` | 120 | **executor** | The seconds of segments the run may spend in all, `pick`'s as well as `manipulate`'s, each charged the seconds its verb said it ran, or the robot's clock across the call when it said none, a call cancelled or aborted mid-segment among them. A segment the robot refused before it began says it ran 0 s. One segment runs at a time, and a `pick` or `manipulate` sent while one runs is refused. Checked before each segment, so the last may run past it by at most its own length, and once it is spent the next `pick` or `manipulate` is refused as a budget. |

`manipulate` must be in `verbs.allow`, and a team task cannot carry a `policy`, because it
hands one arm to its policy. Each segment is still one step against `budgets.max_steps`. A run
whose task has none of this, a `--goal` run, a v2 file, or an MCP session with no task
loaded, gets the defaults above and any instruction. A second task file loaded over MCP is
held to its own section, narrowed from the arm's own verb rather than from the first file's
list, and the seconds of segments the session has run still count, those it ran before any
task file was loaded among them. A decision LLM is offered each listed
instruction and never takes one, whatever `--decision-mode` says: its answer is recorded beside
the pilot's, and the pilot starts every segment
([guides/decision-llms/README.md](../guides/decision-llms/README.md)).

```yaml
---
glot: 3
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

The file allows no `observe`. `roboglot run` checks a task file against the arm as it describes
itself before it connects, which is without one, and refuses a file that allows it. The look
comes from the frame every observation brings the pilot on an arm with a camera.

A plain `roboglot validate` checks a v3 file with no policy server running: the arm offers
`manipulate` to one, so the file is coherent wherever the arm is installed. `--robot NAME`
checks it against that body as it is registered, and a registration holds no policy server.
So on `lerobot:real`, or a robot registered as it, it refuses `manipulate`, and says to start a
server with `roboglot policy serve` and give `roboglot run` its address with `--policy-url`.
`--robot lerobot:mock` checks the file against an arm that has a policy of its own. `roboglot run`
with `--policy-url` checks it against the arm itself.

```console
$ roboglot validate stack-blocks.glot
roboglot validate
+----------------------------------------------------+
| file              | name         | verbs | result  |
|-------------------+--------------+-------+---------|
| stack-blocks.glot | stack-blocks |     3 | + valid |
+----------------------------------------------------+
+ 1 file valid
```

`roboglot run stack-blocks.glot --controller vla --policy-url ...` hands the policy these three
instructions in order and then asks you whether the arm stacked the block
([adapters/lerobot/README.md](../adapters/lerobot/README.md#a-scripted-pilot-that-a-person-judges---controller-vla)).

### `play` (v4)

A task can give the pilot a part to play: an animal, a person, a character, a feeling or a
thing, which it acts out with the body it has ([guides/play.md](../guides/play.md)). `play: true`
in a `glot: 4` file adds one section to the system prompt, `## Playing a part`, directly before
the task file, and nothing else. The tools, the observations and every gate are what they are on
any run, and a file without the key, or with `play: false`, gets not a word of it.
`roboglot run --play` turns it on for one run of any file, a `--goal` included, and `--no-play`
turns a file's `play` off for one run. No variable sets either.

The section is Roboglot's own words and reads no word of the task. Four facts about the run choose
its variant: whether the body can take itself anywhere, whether the run's verbs include `say`, or a
duck's `quack`, whether they include `move_joints`, and whether the run keeps notes. It tells the
pilot that a part is acted out in moves, that the verdict asks whether this body can act it out
rather than whether it is the thing it plays, and that the legs, wings or second arm of what it
plays are left out of `needs`. It also tells the pilot that anything the task asks to happen to a
thing in the world still has to happen for real and is judged as it would be without the part, that
no part brings the body into contact with a person or within reach of one, and that its verdict
reason says plainly what the body will do and every other reason what it actually did. These are
words the pilot reads, and no gate reads them
([concepts/safety.md](../concepts/safety.md#what-roboglot-does-not-protect-against)).

Each member of a team reads the section. `--controller vla` reads no prompt: it refuses
`--play`, and a played file unless `--no-play` turns its part off. Over MCP, a played file's
`robot_load_glotfile` result carries the section's paragraphs, worded for a chat, as `play`.

```yaml
---
glot: 4
name: t-rex
description: Be a T-rex.
verbs:
  allow: [report_state, move_joints, gripper, stop]
requires: [move_joints]
budgets:
  max_steps: 8
success:
  - You have shown what the task asks with the arm's joints and gripper, in moves someone watching could read, and your reason says what the arm did.
persona: "Game for anything: commit to the bit with the whole arm, and say plainly in your reasons what it actually did."
play: true
---
# Task
You are a T-rex. Show me.
```

```console
$ roboglot validate t-rex.glot
roboglot validate
+--------------------------------------+
| file       | name  | verbs | result  |
|------------+-------+-------+---------|
| t-rex.glot | t-rex |     4 | + valid |
+--------------------------------------+
+ 1 file valid
```
