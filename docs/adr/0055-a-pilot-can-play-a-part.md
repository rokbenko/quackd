# ADR-0055: A pilot can play a part

**Status:** accepted · **Date:** 2026-10-08 · Amends [ADR-0032](0032-datasheets-and-the-verdict.md) (a task that gives the pilot a part is judged by whether the body can act it out, and the needs of what it plays are not the body's) and the `.duck` spec, which gains `duck: 4` ([ADR-0019](0019-duck-spec-v1.md)) · Keeps [ADR-0035](0035-one-cli-for-all-your-robots.md)'s "You are the brain of" and [ADR-0005](0005-duck-spec-v0.md)'s rule that the executor enforces the contract and the prompt does not · Implemented in `quackd/agent/prompts.py` (`play_paragraphs`, `play_section`), with changes in the schema, the loop, the CLI, pilot flocks, preflight, the MCP server, the log and the OpenAI provider · Documented in [docs/guides/play.md](../guides/play.md)

## Context

The task files in `docs/examples/lerobot` ask an SO-101 arm for a part as often as for a result:
"You are a cat. There is a cube on the table near the edge. Do what a cat would do, then act
innocent.", "You are a T-rex. Show me.", "Are you a duck? Show me how you feel about the answer."
On the arm, with gpt-6-astra and the lab's install, which reports itself as 0.12.0, Rok
reported about half of them ending with the pilot saying it is a robot and cannot be a duck, a
person or an animal. No transcript of those runs is on the machine this was decided on, so which
tool carried each refusal is not known. What the prompt said is known, and it argued against a
part at every turn:

- It opens "You are the brain of a six-joint desktop robot arm with a parallel gripper (an
  SO-101 class arm driven by LeRobot), bolted to a table", says "It does not move: no base and no
  legs", and lists what the body cannot do under "Whatever the task says, this body cannot:",
  first "go anywhere".
- Before anything moves the pilot has to say "whether this body can do this task at all", and
  `infeasible` ends the run. No sentence said that acting a part out with the body was the task,
  or spoke of playing, miming or pretending at all.
- The verdict gate holds a `feasible` to the body's own datasheet. A pilot willing to play a cat
  that named the cat's legs, `mobility: legged`, or `any`, which 0.12.0 offered where 0.14.0 added
  `none`, would have been refused, as a `feasible` wave naming `mobility: any` was on 23 September.
  Both still are: what 0.14.0 added is `none`, which every body meets. The refusal offers
  `infeasible` first.
- All 223 example files counted "said plainly why this body cannot" as success, 202 told the
  pilot to be "honest about this body ... say plainly when something is beyond you", and 214
  offered failure as the last step of their strategy.
- 0.12.0's prompt still said "Not published: mass, reach. Decline any task that hinges on any of
  them", which 0.14.0 removed.

The same model played along when a task named the body's action. On 2026-09-15 `--goal "Go into
a duck pose and quack with the gripper"` was judged feasible and mimed with the arm, and the same
day "Imagine you're a duck that quacks" moved nothing and declared success with a quack typed
into its reason. Every refusal recorded on the arm quoted quackd's own words back: the
datasheet, the gate, a verb's description, or a joint reading past its travel.

Rok asked for three things to be weighed: (a) dropping the system prompt, (b) keeping it exactly
as it is with a flag or a markdown file to override it, (c) more prompt templates to choose from,
one of them saying the robot is "capable of human or animal like capabilities".

## Decision

- **One named framing, `play`.** `play: true` in a `duck: 4` task file, or `--play` on `run`,
  `record` and `preflight`, adds one section, `## Playing a part`, to the system prompt, directly
  before the task file. `--no-play` turns a file's `play` off for one run, and the flag wins over
  the file. No environment variable and no `robots.json` field sets it, as none sets
  `--policy-url`, so a robot never starts playing every task it is given.
- **The section says four things**, in quackd's own words. A part is acted out in moves, and one
  told only in words has not been played. The verdict asks whether this body can act it out, the
  needs of what it plays are left out of `needs`, and every need of a real result is named in
  full. A real result still has to happen and is judged as it would be without the part, no part
  brings the body into contact with a person or within reach of one, and every limit holds. The
  act may pretend and the record never does. Its sentences about the verdict carry both halves
  of #25's rule, "not by itself" and "mass or size".
- **Four facts about the run choose its variants**, never the task's words (#25): whether the
  body can take itself anywhere, whether the run's verbs include `say`, or a duck's `quack`,
  whether they include `move_joints`, which sets the poses that can reach the table, and whether
  the run keeps notes.
- **Everything else is what it was.** The tools, the observations, the opening, the Rules, the
  datasheet and its `cannot` lines, `ASSESS_TASK`, `own_sheet_objection` and every gate. With
  play off quackd adds nothing to the prompt, which was checked by rendering every shipped body
  with every starter, and the arm with every example file that does not play, before and after
  the change. An example file that now plays reads its new lines with `--no-play` as well.
- **The record keeps the file's contract.** `run_start.play` says whether the pilot was told it
  may play and whose word that was: `task file`, `--play` or `--no-play`. `contract.play` stays
  the file's own, since `--play` on a `duck: 1` file would otherwise record a contract the schema
  refuses. A pilot flock's `flock_start` says the same.
- **Over MCP the part rides on the file.** `robot_load_duckfile` returns a played file's
  paragraphs as `play`, worded for a chat, and a file that does not play loads exactly as before.
- **Refused where nobody would read it:** a coordinator flock, with either flag or a played
  file, which the parser already refuses beside `allocation.method: auction`, and
  `--controller vla`, with `--play`, or with a played file unless `--no-play` turns its part off.
  Then the policy flies the file's instructions and the record says play was off.
- **OpenAI refusals reach the record.** A refusal part on the Responses API and a `refusal` field
  on Chat Completions were dropped, so a model's refusal looked like a model saying nothing. They
  now read `[refusal]` and the refusal's own words, as Anthropic's and OpenRouter's already did,
  and a Responses refusal part with no words still makes the turn a refusal. That tells a refusal
  returned in the API's refusal channel, a safety refusal the model wrote, from a pilot that says
  in its answer that it cannot play the part. The bench counts both. A call quoted in a refusal is
  words, so a local model's text is never read for one in a turn that carries a refusal, or that
  a content filter cut short.
- **The examples opt in where the task is to show something.** 118 of the 223 files under
  `docs/examples/lerobot` are `duck: 4` with `play: true`, and lose the success line, the persona
  and the strategy step that made refusing a way to succeed. 27 of them keep the older second
  step, which asks for small, deliberate moves: those where the shoot's run sheet puts a person,
  a held prop, a camera or a thing within the arm's reach, has someone hold something up in the
  camera's view, rates the experiment as contact or warns that a pose may strike the arm's own
  base, and `e089/less`, whose waves are meant to shrink. The other 105, most of which
  ask for a result on the table, a high five, a guess or a question answered in words, a
  stillness test, a trap or a benchmark, keep every byte. By the run sheet's own expectations,
  195 of the 223 files should run as asked, 87 percent, short of the aim of 90 to 95 percent. The
  28 that should not, where the sheet expects the arm not to do what they ask, in most of them
  as the point of the experiment, are named in the examples README.

## Why not

- **(a) Drop the system prompt.** It carries the Rules, the verbs, the budgets and the datasheet
  every verdict rests on. A pilot without them is refused by the executor more, not less.
- **(b) An override file or flag.** The default stays exactly as it is, which is what (b) asked
  for, but the words added are quackd's, reviewed and tested on every shipped body. Free operator
  text would sit in the most trusted place in the prompt, could contradict the Rules there, and
  would make every measurement of play depend on a file on one machine. `--extra-body` already
  refuses `instructions` for that reason. An append-only `--prompt-note` remains possible later.
- **(c) Templates.** Whole alternative prompts drift from the one that every fix lands in. "A
  robot capable of human or animal like capabilities" contradicts the datasheet further down the
  same prompt and invites the needs the gate refuses. One named framing is (c) at its narrowest.
- **An enum or a template name.** "mode" is already `--decision-mode` and the header's `mode`
  row, which says DRY RUN, and `mode: work` would rename a default that must stay byte-identical. A
  second framing, if one is ever wanted, is a new key under a new spec version.
- **A keyword heuristic.** quackd cannot know what a sentence needs (#25), so nothing turns play
  on from the words of a task.
- **Rewording `ASSESS_TASK`, `own_sheet_objection` or the opening.** They are the gate's own
  words, shared with MCP and pinned by tests, and "You are the brain of" is ADR-0035's.
- **A variable or a per-robot default.** Either would make play sticky.
- **A permanent golden of every prompt.** It would move with nearly every prompt fix. A
  structural test holds the lasting property instead: a played prompt is the plain one plus one
  section, on every shipped body.

## Consequences

- `duck: 4` files are refused by older quackd versions, which is the correct failure.
- A played prompt is longer by one section, on every turn.
- The act is prose. Whether a mime is reported as a real result is held only where a gate or a
  sidecar check reads the world, and otherwise by the record and whoever reads it. That no part
  comes within a person's reach is prose too: no gate sees a person, the pilot learns of one only
  from its detector and its pictures, and the colour detector, the default, reads blue as one.
- A played run moves where a plain one might have stopped. That is its purpose and its risk,
  which is why it is opt-in per file or per run and why the header and the record say when it
  was on.
- A body whose only camera rides on it has no picture of itself and the space around it, so its
  pilot is asked to keep every move of a part small and slow, as the section asks of a pilot that
  cannot see who is within reach.
- Played runs have not run on the arm. The measurement is a bench session: the same files as they
  read at v0.17.1, as they read now with `--no-play` and as they read now, read for runs that
  never moved, or moved and then disowned the part, with a refusal returned in the API's own
  channel counted apart.
