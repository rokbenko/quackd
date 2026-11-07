# ADR-0077: A body quackd only describes is one YAML in the core

**Status:** accepted · **Date:** 2026-11-05 · Follows [ADR-0075](0075-an-adapter-is-a-family-a-body-file-and-a-binding.md) (a body is data) and [ADR-0076](0076-every-backend-has-a-tier-and-bound-code-names-its-owner.md) (the described tier) · Extends [ADR-0032](0032-datasheets-and-the-verdict.md) (a body is figures with a confidence and a source) and [ADR-0073](0073-a-robot-carries-its-own-passport-and-its-owner-signs-it.md), whose demo bodies are described bodies in the passport's format · Implemented in 0.33.0 in `quackd/adapters/described.py` and `quackd/adapters/bodies/` · Documented in [docs/adapters/status.md](../adapters/status.md)

## Context

A robot quackd knows about and does not drive already exists in two places. `quackd onboard`
runs a body described by its owner's `robot.yaml` with no `robot:` section: a mock that
reports a state and stops, under a manifest built from what the owner wrote, so a passport can
be served and a brain can talk for a robot whose own software walks and grips. And the
passport demo's two bodies, a Unitree G1 EDU and an Agile ONE SN, are that kind of file,
their figures read from their makers' pages with a date each.

A pilot plans from a datasheet before anything moves ([ADR-0032](0032-datasheets-and-the-verdict.md)),
and a flock's bids carry each member's figures, so a body whose figures quackd knows is one a
flock can plan with, driven by somebody else or not at all. Fifteen more robots are wanted,
industrial arms, quadrupeds and humanoids among them, most with a public control path and none
with an owner here, so by [ADR-0076](0076-every-backend-has-a-tier-and-bound-code-names-its-owner.md)
none of them may enter as bound code.

Two shapes were weighed. A tenth catalogue row, `described`, whose backends are the bodies'
names, reached through the one core module the factory already looks up by name. Or a
registry kind of its own beside the adapters. `parse_robot_spec`, `_installed()`, `_module`,
`describe`, `make_adapter`, `registry_for`, the registry's `_check_spec`, `--robots`, a pilot
flock's roster and the page `quackd ui` draws all go through `adapter_names()` and `info()`,
so one row and one module make `described:unitree-b2` work in every one of them with no new
branch, where a kind of its own needs a case in each and a second table in doctor.

## Decision

**A described body is one YAML under `quackd/adapters/bodies/`, shipped in the core wheel, and
the catalogue's tenth row, `described`, lists every one of them as a backend.** So
`quackd robot add b2 described:unitree-b2` registers one, `quackd list-verbs --robot b2` shows
its two verbs, a flock roster may name it, and `quackd list-adapters` prints the row with the
tier described on every backend.

**The file is the manifest's own fields, minus what a described body cannot have.** `vendor`,
`model`, `embodiment`, `mobility`, `blurb` and the `datasheet` with a source and a confidence
per figure, as the manifest's `Datasheet` has always demanded. It may not carry `id`, `verbs`,
`backend`, `intents`, `sensors`, `limits`, `preconditions`, `extras`, `safety_authority`,
`frame` or `manifest`: the loader refuses any key but those six by name, as a claim a body
nobody drives cannot make, and builds the `RobotManifest` with `report_state` and `stop` as its
only verbs, no intents and `extras.described` saying `maker`, where a robot.yaml's says true.
Inside the datasheet, pydantic's `extra="forbid"` refuses a typo by its path, under the file's
name. A figure nobody published is left out, and the prompt says "not published" as it does for
every body; one the maker's own page leaves out and another source gives is marked `estimate`
and names it. `sensors` is refused with the rest because a manifest's sensors are what quackd
reads, and a verb's requirements are judged against them: a described body that listed the
camera its robot carries would be offered to a flock as a body that can look. What the robot
carries is a note in its datasheet, and a board with a camera beside the body (`--host`) gives
it `observe`, as it gives any body.

**It is honest about what it is.** Its blurb ends by saying quackd drives no part of it, and
its prompt says so before any figure: that it is described from its maker's figures, that no
verb here moves it, and that it moves only if someone else moves it. `make()` refuses an
address, a camera, a token and a rest pose, since there is nothing to reach. The registry
refuses the first three at `quackd robot add` and `robot edit`, and `quackd robot rest-pose`
records no rest pose, before a run could, while a hand-edited line that says one takes no other
robot in the file down with it. A bare `--robot described` names no body and is refused with
the list, where a bare adapter's name means its first backend. `quackd robot list --probe` says
there is nothing to reach, with neither a tick nor a cross, since a tick would read as a robot
that answered, and `quackd announce` refuses one, since there is no robot on the network to
announce. Its posture reads `unknown`, nothing reports its falls, its health says quackd reads
nothing from it, and its `stop` says there is nothing to stop. `DescribedAdapter` moves from
`quackd/onboard/body.py` to `quackd/adapters/described.py` and `quackd onboard` builds the same
thing from a `robot.yaml` as it did.

**The counts stay true, and a described body is never offered as one to drive.** `kind` on the
catalogue row keeps the nine adapters nine: `ADAPTER_NAMES` is the rows of kind adapter, and
the described bodies are counted apart, on the badge and in doctor's "adapters installed"
line. A command given no `--robot` never falls back to one, so a core with no adapter installed
still says no robot adapter is installed. `installed_manifests()` and `bodies_that_could()`
stay the bodies a task can be handed to, and so does the `could` list an MCP client is given.
The described ones are `described_manifests()`, each named by the spec that reaches it, and a
hint that one of them meets a task's needs is a sentence of its own, last, that says quackd
drives no part of it. A `--json` row of `list-adapters` has `kind` `described`, and `installed`
and `adapter_installed` false, so a script asking whether this machine has a robot gets the
answer it always got.

**A table of them is generated**, into [docs/adapters/status.md](../adapters/status.md), from
the files, by a script with a `--check` the suite runs, the way the LLM tables are rendered
from their catalogues (ADR-0062).

## Consequences

- 0.33.0 ships eleven described bodies, chosen for having a public control path: the Unitree
  B2, Go2 EDU and G1 EDU, the Rhoban Microban, the Menlo Asimov-1, the Noetix Bumi, the Franka
  Research 3, the Yaskawa MotoMini, the KUKA LBR iisy 3 R760 and LBR iiwa 7 R800, and the
  Universal Robots UR5e. The Agile ONE SN (no public API, available 2027) and the industrial
  UBTech Walker S2 (certified operators only) stay out; a Walker that joins later is the S2.
  Every figure cites the page it was read on and the date: the maker's own, but for two marked
  `estimate`, the B2's joint count from a dealer's listing and the LBR iiwa's weight from a 2016
  KUKA presentation.
- A quadruped's load on its back is a note, not a figure: the datasheet refuses `payload_kg`
  with no manipulator, and a `carries_kg` figure is a change to the manifest every peer reads,
  so it waits for a record of its own.
- An industrial arm that ships with no gripper is declared `manipulator: arms` with one arm, so
  that its payload and its reach can be stated. One bare arm closes on nothing, so the prompt
  calls it a bare wrist that holds nothing until somebody fits a tool, and a task that needs
  hands is not met by it. Where a maker gives a range, the lower end is the figure.
- A described body cannot do anything: a goal given to it ends at `report_state`, `stop` and
  the verdict its datasheet earns it. That is the point, and what a flock gets from it is a
  member whose figures it can plan around.
- No described figure has been checked by a second reader, and the bodies' makers were not
  asked. The table says when each was read.
- The one thing being in this repository buys a described body, as ADR-0037 said of an
  adapter, is a row on a machine where nothing is installed: here, a row on every machine,
  since the core carries the files.
