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
   own `stop` RPC ends it, which the cockpit cannot call, and so does the end of the whole
   stack, which ends the arm's run with it.
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
- **The base stack's own navigation comes from the bridge.** MOSS's launcher refuses to start
  beside any dimOS run, so moss-arm starts after the base stack, and the cockpit reads only the
  newest run: with the arm up it shows no exploration and defers no goal. The bridge tells the
  base stack from the arm's run and reads the base stack's nav channels itself, quackd takes its
  nav from there, and `drive_to` stops an exploration the cockpit cannot see before its goal.
  Without the bridge, a cockpit that shows moss-arm is refused at connect.
- **The bridge shapes every twist on the rover**, and no core verb is overridden. An arc keeps
  `|wz|` at or under `2|vx| / track` with the stack's own track width, and a turn on the spot is
  the maker's own pulses, timed on the rover. After the firmware's 0.3 s with no new twist it
  sends one zero and then nothing, because of the second trap.
- **The stop is both surfaces, every time**: the bridge's `/stop` and the cockpit's soft stop,
  sent at once, so neither waits for the other. Off `moss-litter` the bridge's zero goes before
  its arm cancel, so a hung coordinator never holds back the zero, and every stop and keepalive
  names the base run quackd connected to, so a stack the owner restarted since is left alone.
  On `moss-litter` the bridge sends the mission's stop first and then at once cancels the arm
  and sends the zero, because a cycle cancelled under a mission not yet told to stop is a failed
  pick and the mission explores again. Only then does it confirm the mission's stop, by its RPC
  falling silent, counted only from a mission the bridge heard answer before, so the arm is
  never held back for the confirmation. The mission's stop leaves its own pick running, so the
  bridge ends the maker's script first, SIGTERM and then SIGKILL, before it cancels: a pick
  still starting would otherwise send its trajectory after the cancel. quackd never posts the
  cockpit's `/cmd/stack`, and its close never sends a go limp call.
- **The close halts what its session started.** After `collect_litter` it is the whole stop.
  Otherwise it is the session's own arm cycle, cancelled by its id, a twist of its own the
  bridge may still be repeating, and a goal or an exploration it started. A session that
  started nothing sends nothing when it closes, and one that sent no stop either leaves no
  note, so `quackd doctor` on `moss-litter` reports a healthy rover and leaves the mission
  running.
- **A session deadman, one per session.** Each quackd transport draws a session id and keeps it
  alive from its loop's own heartbeat, a keepalive first in every beat, so a dead process, a
  dead link and a wedged loop all end it. Each keepalive declares the bridge's own 1 s cap, so
  the window is 3 s, and three missed periods later the bridge halts what that session said it
  started through either surface and nothing else, and says so in the keepalive answers after.
  A halt of something the session started fails its heartbeat. A prompt holds the loop, and
  nothing is started while one is on the screen, so a halt then halts nothing. A transport
  that kept its session alive from a thread of its own was tried first and reversed: it kept
  a wedged quackd's exploration running to its radius. A start is said to the bridge before it goes
  out. On `moss-litter` the whole stop runs only for a session that started the mission. That
  gives `drive_to`, `explore` and `collect_litter` a deadman whenever the bridge is up, and the
  manifest claims `deadman: true` only then.
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
  mission, the one at the end of every run among them, and it stays ended until the owner
  restarts the stack. A `quackd doctor` sends none.
- A run that moved the arm and ends with it out of its drive pose leaves it holding, and the
  cockpit's idle stop will later end the arm's run and its torque. The close note says so.
- The pulses' turn rate, where the jaws close, the clock of a nose frame's stamp and whether an
  outside stop ends the mission cleanly are UNVERIFIED, each with what quackd does about it, and
  the hardware checklist measures the first.
- The bridge is a second protocol of quackd's own, pinned at both ends by tests that compare the
  two files, and a mismatch is refused by name.
- Six things in the cockpit would retire the bridge. The adapter's page lists them for MOSS's
  maker.
- Nothing here has run on a rover.
