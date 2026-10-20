# ADR-0056: MOSS is reached through its own cockpit and a bridge of quackd's

**Status:** accepted · **Date:** 2026-10-20 · Follows [ADR-0037](0037-adapters-are-their-own-packages.md) (the adapter is its own package, `quackd-moss`) and [ADR-0046](0046-the-jetson-is-reached-not-run-on.md) (quackd stays on the laptop and reaches what runs on the robot) · Amends [ADR-0012](0012-safety-executor.md): a verb of the `dangerous` class needs a person to answer its confirm · Implemented in `adapters/moss/` and `bridge/moss/quackd_moss_bridge.py`, and the rule in `quackd/safety.py` · Documented in [docs/adapters/moss/README.md](../adapters/moss/README.md) and its [hardware checklist](../adapters/moss/hardware-checklist.md)

## Context

MOSS is a small tracked rover that picks up litter, by Laurent Genoud (Show Robotics), read at
`d288647` on 2026-10-20. Its software is `moss_dimos`, a blueprint package for dimOS, read at
`a7d19f7`, the commit MOSS's own Software.md installs. Its maker says it drives and demonstrates
pick and drop motions, and that autonomous litter picking and the full field test are not yet
demonstrated.

Its only network surface is its cockpit, `tools/moss_cockpit.py`: an HTTP server with no
authentication that reads the rover from the stack's LCM bus and sends a goal to the planner,
starts and stops the explorer, sends a soft stop and runs MOSS's launcher. It has no velocity
command and no arm command. Everything else is dimOS: LCM on the rover and pickled RPC on that
bus, which quackd on a laptop should not join.

Four things read at the pin shape everything below.

1. **The base has no slow pivot.** MOSS's base adapter raises any moving track below 20% to 20%, and
   sends every turn on the spot, and every arc whose inner track reverses, at 100% on both
   tracks. Its own mission turns in pulses.
2. **Every message on the pad's channel cancels navigation**, a zero included
   (`MovementManager._on_teleop`), and the cockpit's soft stop relies on exactly that.
3. **The litter mission listens to no stop.** Its inputs are objects, odometry and
   `goal_reached`. From the moment a `moss-litter` stack starts it goes for any confirmed litter
   in view, and after a pick, or a failed one, it sets the rover exploring again by itself. Its
   own `stop` RPC is the only thing that ends it, and the cockpit cannot call it.
4. **MOSS's hard stop drops the arm.** `tools/moss-fly.sh stop`, which the cockpit's idle stop
   runs too, ends every dimOS run, and the arm's adapter turns the torque off on all six servos
   as its run ends. Killing the maker's arm script cancels nothing: the whole cycle is one
   trajectory, and only `cancel_trajectory` stops it, after which the arm holds.

## Decision

- **Two surfaces.** quackd speaks MOSS's cockpit for the state, the pose, a goal for the planner,
  the explorer and the soft stop, and ships a bridge of its own, `bridge/moss/`, for what the
  cockpit has no command for: a twist with a deadman, a frame that says when it was taken, the
  maker's arm cycle with a cancel, and the litter mission's own stop. The bridge runs on the
  rover with MOSS's own interpreter, imports quackd nowhere, binds loopback, takes a token, and
  is reached with the cockpit through one ssh tunnel. quackd imports neither MOSS nor dimOS.
- **The bridge shapes every twist on the rover**, and no core verb is overridden. An arc keeps
  `|wz|` at or under `2|vx| / track` with the stack's own track width, and a turn on the spot is
  the maker's own pulses, timed on the rover. After the firmware's 0.3 s with no new twist it
  sends one zero and then nothing, because of the second trap.
- **The stop is both surfaces, every time**: the bridge's `/stop`, then the cockpit's soft stop.
  On `moss-litter` the bridge stops the mission first, then cancels the arm, then sends the zero,
  because a cycle cancelled under a mission still running is a failed pick and the mission
  explores again. The mission's stop is confirmed by its RPC falling silent, counted only from a
  mission the bridge heard answer before. quackd never posts the cockpit's `/cmd/stack`, and its
  close never sends a go limp call.
- **A session deadman.** quackd's heartbeat keeps a session open on the bridge, and after three
  missed periods the bridge halts what quackd started through either surface and nothing else.
  That gives `drive_to`, `explore` and `collect_litter` a deadman whenever the bridge is up, and
  the manifest claims `deadman: true` only then.
- **What answered decides the verbs.** `describe()` declares every stack's verbs together, and
  connect narrows them to the stack that runs and what answered: twists and the arm need the
  bridge and a base stack, the planner's verbs need `moss-explore`, and `collect_litter` needs
  `moss-litter` with the bridge and the mission answering. `moss-litter` without them is refused,
  because nothing else could stop the mission.
- **`collect_litter` is `dangerous`,** and `explore`, `pick_and_drop` and `stow_arm` are
  `confirm`. With this release a `dangerous` verb runs only when a person actually answered its
  confirm: `--yes`, a flock's standing answer, the MCP server and a piped `yes` all refuse it,
  with a gate that names the fix. No verb shipped before `collect_litter` was `dangerous`. The
  executor refuses one before its confirm wherever `a_person_was_asked` says nobody was.

## Why not

- **Speak dimOS directly from the laptop.** dimOS is the rover's own framework, installed in
  MOSS's environment there, and as a dependency of quackd it would land on every laptop. Its RPC
  is pickled Python, and an LCM bus is not something to join from another machine on a shared
  network. The cockpit already reads the bus on the rover, and the bridge does the rest there.
- **A twist through the cockpit's goal.** A goal is not a velocity: it has no deadman and no
  way to turn on the spot slowly, and `go_to` and `search_scan` need both.
- **Mirror the maker's arm cycle in quackd.** The cycle, its poses and its lag watch are the
  maker's and change with his calibration. The bridge runs his script as his own mission does,
  and reads its lines and its exit code.
- **Leave `moss-litter` out.** Rok chose to keep it, with the mission's own stop and the
  `dangerous` rule, and to refuse it wherever that stop cannot be reached.

## Consequences

- `collect_litter` runs once per stack start. Every stop quackd sends on `moss-litter` ends the
  mission, the one at the end of every run and a `quackd doctor`'s close among them, and it stays
  ended until the owner restarts the stack.
- A run that ends with the arm out of its drive pose leaves it holding, and the cockpit's idle
  stop will later end the arm's run and its torque. The close note says so.
- The pulses' turn rate, where the jaws close, the clock of a nose frame's stamp and whether an
  outside stop ends the mission cleanly are UNVERIFIED, each with what quackd does about it, and
  the hardware checklist measures the first.
- The bridge is a second protocol of quackd's own, pinned at both ends by tests that compare the
  two files, and a mismatch is refused by name.
- Six things in the cockpit would retire the bridge. The adapter's page lists them for MOSS's
  maker.
- Nothing here has run on a rover.
