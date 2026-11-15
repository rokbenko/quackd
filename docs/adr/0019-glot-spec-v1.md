# ADR-0019: `.glot` spec v1

**Status:** accepted · **Date:** 2026-09-02 · Implemented in Phase 1 of 0.4 ([design](../design/multi-robot.md))

## Context

ADR-0005 fixed `.glot` v0: an enforced YAML contract plus a Markdown body, strict parsing,
`glot: 0` as the only version. A multi-robot Roboglot needs a task to say which verbs it
*needs* (not only which it *allows*), which robots it expects, and, for a team, which
roles exist and what each requires. The format name does not change: `.glot` is the format
the way `Dockerfile` is, whatever the robot.

## Decision

- **`glot: 1`** adds three top-level keys and two team keys, all optional:
  - `requires: [verbs]`: the verbs the task needs. `roboglot validate --robot <spec>` (one
    or more) checks them against each robot's manifest and reports field-level errors of
    the form `requires kick, but arm-01 (lerobot-so101) does not provide it`.
  - `robots:`: a string (`adapter:backend`, the solo default so `roboglot run <task>` needs
    no flags) or a mapping from team member name to spec.
  - `team.roles: {spotter: {requires: [...]}, kicker: {requires: [...]}}`. In 0.4 the
    role names are restricted to exactly `spotter` and `kicker`, because only those two
    behaviours exist in code; a requires-only role with no behaviour would be a fabricated
    capability. `count` is fixed at 1.
  - `team.frame_hints: auto | on | off` (see ADR-0020).
- **`glot: 0` files parse and run unchanged.** Any v1 key under `glot: 0` is an error that
  names the fix (`requires needs glot: 1`). The only new rejections that can hit a v0 file
  are two contradictions no shipped task contains: listing a verb and its alias in `allow`,
  and putting `stop` in `confirm`.
- **For `glot: 0`, `effective_requires` is `verbs.allow`.** A v0 task needs everything it
  allows; that is what makes `validate tasks/find-and-kick.glot --robot lerobot:mock`
  fail on `kick` with the wording above, and also makes `patrol-and-quack` fail on `walk`
  against a LeRobot arm, which is correct.
- Validation lives in one place, `roboglot/glotfile/validate.py::validate_glot(duck,
  manifests)`, returning `Problem(field, robot, verb, message)` rows, and is shared by
  `roboglot validate`, the MCP `robot_load_glotfile` tool and the team runner.
- `validate` without `--robot` uses the task file's own `robots:` default, then the Microduck
  vocabulary, so CI's `roboglot validate tasks/*.glot` keeps working for a starter with no
  `robots:` key, like `find-and-kick.glot`.
- The bundled starters stay `glot: 0`; only the two new tasks ship as v1.

## Consequences

- `roboglot/glotfile/schema.json` is regenerated and the drift test covers the v1 keys.
- A `.glot` can be checked against a robot it has never run on, offline, which is the
  precondition for community tasks targeting robots the author does not own.
- Old Roboglot versions refuse v1 files (strict parsing), as they refuse `team:` today; the
  spec documents this as intended.
