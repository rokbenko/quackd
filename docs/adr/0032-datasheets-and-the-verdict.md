# ADR-0032: Datasheets, and a pilot that refuses what its body cannot do

**Status:** accepted, amended · **Date:** 2026-09-13 · Extends [ADR-0017](0017-robot-adapters-and-manifest.md) (the manifest gains a datasheet), [ADR-0012](0012-safety-executor.md) (a gate before motion) and [ADR-0019](0019-duck-spec-v1.md) (`duck: 2`)

**Amended 2026-09-13 by [ADR-0034](0034-registered-robots-and-pilot-flocks.md):** the
consequence below that no flock quackd can start has two different bodies in it is now true of
the *auction* only. A pilot flock takes any bodies, so the datasheet vocabulary this ADR
defines is finally read by something that moves: each pilot's `assess_task` judges its own part
of a shared goal against its own sheet, and the "Your flock" section of every pilot's prompt
carries `body_summary` for each of its peers, which is the same paragraph this ADR renders for
the pilot's own body. A pilot flock uses no `flock.roles`, so role `needs` is still exercised
only at the coordinator and in unit tests.

**Amended 2026-09-17 by [#26](https://github.com/rokbenko/quackd/pull/26):** the gate reads one
thing beside `BEFORE_VERDICT`, the verb's own `read_only` flag, which an adapter already sets so
that a sensing verb runs under `--dry-run`. The set below names the verbs quackd ships, and the
test that holds every shipped verb to it also rejects a name no shipped adapter offers, so the
set is closed to a body quackd never shipped: such a body's own sensing verb was refused as
"moves the body", and its pilot was denied the one verb that answers the question the gate is
waiting for. Measured on a humanoid sim offering `locate` and `reach`, a local 14B model judged
the task infeasible twice without a single look. A verb that sends no intent cannot move the
body, so the flag is a sound thing to open the gate on, and it was already trusted with more:
`--dry-run` runs a read-only verb against real hardware. What still holds: the shipped set is
closed and every shipped read-only verb is in it, which a second test now pins in both
directions; a stranger's verb that speaks or turns a head is motion and still waits; and a
learned verb never carries the flag, so it is refused until somebody classifies it on purpose.
The sentence below that anything not in the set waits is true of everything quackd ships and of
every verb that is not read-only.

**Amended 2026-09-17 by [#24](https://github.com/rokbenko/quackd/pull/24):** a `feasible`
verdict is no longer recorded unconditionally. `needs` was defined here as what the task
requires in the datasheet's own field names, and it was read by the matcher, by a role and by
the coordinator judging a bid, but never against the sheet of the body the pilot was about to
drive. So a verdict could say, in its own two fields, both that the task needs 45 minutes of
running time and that this body is fine, on a body whose endurance nobody published, and the
gate opened. Measured on Qwen3-32B-AWQ: `feasible` six times out of six on that task, and the
duck walked until its step budget ran out. Both surfaces now run `missing_needs` against the
pilot's own manifest before recording, refuse such a verdict the way one carrying `human` is
refused, name the unmet need, and withdraw any verdict that was standing, so the gate shuts
rather than leaving an older `feasible` to carry the motion. `uncertain` and `infeasible` are
untouched: one asks a person, and the other ends the run.

The refuse-by-default rule above is what makes this bite, and it keeps two exceptions so that
an honest answer is not refused: a minimum of zero asks for nothing, and a body that published
no terrain meets `indoor_flat`, which is what the prompt already tells such a body to assume
about itself. Both live in `missing_needs_in`, so the coordinator judges a bid by the same
reading.

What it does not do, which is the same shape as this ADR's own "what this does not protect
against": the check reads what the pilot declared about the task, so a pilot that never names
the figure its plan hinges on passes exactly as before. It rewards honesty and cannot catch
silence, and a pilot that names a figure this body's maker never published is asked to answer
again rather than allowed to proceed. The route out of that for a person who knows the figure
is the `duck: 2` `datasheet:` block above, which is the only thing that makes a sheet say
something new.

**Amended 2026-09-23, after the first SO-101 bench runs of the shoot examples:** the check
#24 added refused the honest answers of a body this ADR never had in front of it, one that
does not move. The pilot was told to fill `needs` in even for a feasible verdict and that a 0
asks for nothing, while the checker left `work_height_m: 0` out of that rule, offered no word
for a task that goes nowhere (`any` means some kind, which a bolted arm fails), and refused a
flat indoor floor on a body whose prompt says "it does not move" where a moving body's names a
terrain. Nearly every run stopped at the `uncertain` that followed and its y/N question. So
`none` joins the `mobility` and `manipulator` words and asks for nothing, a zero asks for
nothing on every number, a body that does not move meets `indoor_flat` and is refused anything
above it as `(it does not move)`, and every field in the tool schema says what it means. The
two exceptions of the amendment above are four. One reading is also kinder to a pilot's own
sheet than to anybody else's: an unpublished working height band does not refuse the pilot's
own verdict, because the prompt never lists a working height as missing, while the
coordinator, a flock role and the list of bodies that could stay strict, since there an
unknown is still not a yes.

The prompt's "Not published" line no longer says to decline any task that hinges on one of
them, which on an arm with no published reach forbade reaching for anything. It tells the
pilot to answer `uncertain` and name the figure, which is the way out the gate already
offered, and the sentence under Decision that the prompt renders an unpublished figure as
"decline" is superseded by it. The SO-101 sheet publishes a reach, as an estimate summed from
the link lengths in the maker's URDF, and its payload line gives the pilot a scale to judge an
object by rather than ruling out everything nobody weighed. And a pilot whose `uncertain` a
person answered with go is told so, and not to assess the same doubt again: told only that
motion now ran, the bench's pilot asked again, as `infeasible`, and ended its run. The rule
that a figure nobody published is not met stands.

**Amended 2026-10-13:** the Microduck's note that the decision below describes was wrong about
where its cap is. robotd clamps no twist at any release quackd has read: what limits a tick in
its `robot.state` is the deadman, a joint target past the actuator's travel, or one that is not
finite, and nothing caps the speed of the twist its policy is handed. The caps, 0.3 m/s forward,
0.2 m/s sideways and 1.5 rad/s of turn, are the Microduck's `manifest.limits`, which quackd
holds every twist a verb sends to (`speed_limits` in `quackd/verbs/core.py`), as the decision
says of every body. The note says so now, and the numbers are as they were.

## Context

A pilot was told one line about the body it was driving, `manifest.blurb`, and a list of
verbs. Nothing numeric: no payload, no reach, no working height, no endurance, no terrain
rating, and no way to say "this body cannot do that at all". A model asked to carry a
laundry basket on a 25 cm duck had no ground to refuse on, and the only way out was
`declare_failure`, which means "I tried and could not" and is recorded as a failure of the
attempt rather than a judgement about the body.

What physical facts did exist were scattered: a few numbers inside blurb prose ("25 cm,
800 g"), a warning inside a verb description ("this is a 12 kg cart"), and hand-written
paragraphs in individual `.duck` bodies that the next task file had to write again. None of
it was data, so nothing could match a task against a body, and nothing could say which other
body could do what this one could not.

## Decision

**The datasheet is structured, and every number says who says so.**

- `RobotManifest.datasheet` is a `Datasheet`: `mass_kg`, `height_m`, `dof`, `payload_kg`,
  `reach_m`, `workspace_height_m`, `endurance_min`, plus `manipulator`, `arms`, `tethered`,
  `terrain`, `not_rated`, `cannot` and `notes`. Each number is a `Figure` with a value, a
  `confidence` (`official`, `estimate` or `measured`) and a **required** `source`: a figure
  without one is a rumour.
- `None` means not published. The prompt renders it as "not published: decline any task that
  hinges on it" and never as a zero. Refuse by default is the rule, because a wrong number is
  worse than an absent one.
- **Categorical constraints come before numbers.** `cannot` is a list of sentences, and the
  highest-value ones need no precision at all: an SO-101 cannot go anywhere, an Open Duck Mini
  cannot pick anything up. These prevent the errors that numbers cannot.
- **Speeds are not in it.** `manifest.limits` is what quackd clamps to, which is a rule about
  what quackd sends, not a fact about the body. The prompt renders those separately, as
  clamps, and the Microduck's note says its cap is a software clamp in `robotd` rather than a
  measured maximum.
- **One sheet per body, whatever the backend.** sim2d, mock or real, the body is the body.
  This is also what keeps a robot's `digest()` equal across its backends. The one exception is
  `rosbridge`, below, which names a transport rather than a body.
- `manifest: 1` is unchanged: the field is optional and additive, and no wire consumer parses
  a whole manifest across versions (mDNS carries a digest, the flock bus carries verb names,
  MCP returns per-field payloads).

**A task file can correct the sheet for the build in front of it.** `duck: 2` adds a
`datasheet:` block: a figure given there replaces the adapter's and is rendered as coming from
the task file, so a reader can tell the maker's numbers from yours. The sentence lists extend
and never delete: a task file can add a `cannot`, it cannot remove one. A correction the body
contradicts is refused by `validate` before anything connects. A flock duck cannot carry one,
because it describes one body.

**Nothing moves until the pilot has judged the task against it.**

- A new meta tool, `assess_task` (`robot_assess_task` over MCP), records a verdict:
  `feasible`, `infeasible` or `uncertain`, with the reason, the datasheet fields consulted,
  the model's own estimates about the world (object, quantity, value, basis, confidence) and
  `needs`: what the task requires, in the datasheet's own field names.
- The executor gains a `verdict` gate, after the allowlist and before the params check, so a
  typo still reads "unknown" and a disallowed verb still reads "allowlist". It refuses every
  verb that moves the body until a feasible verdict exists. `BEFORE_VERDICT` names what runs
  first: `stop`, `observe`, `report_state`, `say`, `quack`, `express`, `gaze`, `look` and
  `introspect`. A pilot has to be able to look at the thing before judging whether it can lift
  it. Anything not in that set waits, including a verb quackd has never heard of, and a test
  asserts every verb every shipped adapter offers was classified deliberately, in both
  directions.
- **`infeasible` is its own outcome**, not a `failure`. `failure` means the robot tried;
  `infeasible` means nothing moved and another body should be asked. `quackd run` exits 3 for
  it, which is what a script trying one body after another branches on, and the run's reason
  carries which shipped bodies could do it, read from their static datasheets.
- **`uncertain` asks a person.** At a terminal that is a y/N prompt, and a no ends the run
  `aborted`, because a person stopping a robot is the kill switch's kind of act. Over MCP
  there is no terminal, so it stays pending and the model is told to ask the person it is
  chatting with: `--yes` does not clear it, because a reachable human is a better answer than
  a flag. With nobody to ask at all, the pilot is told to decide on its own responsibility
  rather than being cleared by default.
- **A model cannot clear its own doubt.** `assess_task` has no `human` field, and a verdict
  that arrives with one is refused rather than quietly stripped.
- **A flock member is never asked.** It is a state machine with no pilot, so
  `Executor.require_verdict` is off by default and only the agent loop and an MCP session turn
  it on.
- **The scripted pilot answers the gate as a rule** and says so in its reason, because a rule
  has no judgement of a body. That keeps every keyless example and all ten acceptance seeds
  running unchanged.

**rosbridge asks the bridge what the body is.** It is the one adapter whose name says nothing
about a robot, so at connect it reads the topic list from `/rosapi/topics` and the robot's own
description from the `robot_description` parameter, falling back to the latched
`/robot_description` topic. Two things come out of a URDF that it can honestly settle, the sum
of the link inertials and the count of joints that are not fixed, and both are tagged
`official` and sourced to the file. Everything else stays unknown, payload above all. One
deadline covers the whole look and a failure never fails a connect: a bridge without `rosapi`
leaves the sheet saying nothing was discovered, with the reason. A new verb, `introspect`,
asks again.

**A flock role can ask for a body.** `duck: 2` adds `needs` to a role, in the same vocabulary
as a verdict's, so a refusal and a role say the same thing. An unpublished figure counts as
not met. It is checked three times in the same words: by `validate`, by the member before it
bids, and by the coordinator from what the bid itself carried, so a robot quackd does not run
is held to the same standard.

## Consequences

- Every shipped manifest's digest changes. Nothing pinned a digest value, and the tests that
  compare digests compare them *between backends of one robot*, which still hold because a
  sheet is constant per adapter.
- Both schema files are regenerated (`python -m quackd.adapters.export`,
  `python -m quackd.duckfile.export`), and older quackd versions refuse a `duck: 2` file,
  which is the correct failure.
- The numbers were read from the makers' pages, repositories and one paper on 2026-09-13.
  **None of them were measured here.** That is what the confidence labels are for, and a body
  whose maker never published a figure says so rather than having one invented for it.
- What this does not protect against: the verdict is the model's own judgement against numbers
  that themselves carry confidence labels. A wrong `feasible` is still caught by every gate
  below it, the allowlist, the budgets, the confirm gates and the robot's own safety authority.
  A wrong `infeasible` costs a run and nothing else. A body that lies about itself in its own
  URDF is believed, and the assumption is recorded as such.
- The browser demo (`web/`) has its own loop and its own hardcoded prompt, and has neither a
  datasheet nor the gate. It is noted in `PLAN.md` rather than left to be inferred.
- A `duck: 2` flock role with `needs` validates and matches, but `flock/runner.py` still knows
  only the Microduck, so no flock quackd can start has two different bodies in it. The words
  are in place for the day it does, and the matching is tested at the coordinator.
