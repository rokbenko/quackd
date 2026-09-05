# ADR-0024: Open Duck Mini v2: quackd ships the daemon that runs on the robot, and vendors nothing

**Status:** accepted · **Date:** 2026-09-03 · Extends ADR-0017, ADR-0022 · Amends ADR-0003 · Follows the `say` precedent of ADR-0023 · Implemented in 0.5 ([design](../design/open-duck.md))

## Context

The Open Duck Mini v2 is an open hardware 3D printed biped that people are building at home
today. That makes it the first robot quackd supports whose hardware an outsider can actually
own: the Microduck ships around Christmas 2026, and the Reachy, LeRobot and rosbridge
backends are all "verified names, never run against the real thing".

Read from upstream source on 2026-09-03, at `apirrone/Open_Duck_Mini_Runtime` commit
`3203734` (branch `v2`) and `apirrone/Open_Duck_Mini` commit `b23317a` (branch `v2`), the
robot verifiably has: a 50 Hz ONNX walk policy running on a Raspberry Pi Zero 2 W, fourteen
Feetech STS3215 servos, a seven element command vector of `vx, vy, vyaw` plus four head
angles, a BNO055 IMU, and optional eyes, projector, antennas, speaker, microphone and camera
that `duck_config.json` declares one flag at a time. It verifiably does **not** have a beak,
a gripper, a kick policy, a sit policy, a get-up-after-fall policy, a battery readout, or a
network control API of any kind. Its only command source is a local pygame gamepad, and its
only socket checks the IMU.

Two further facts shaped everything. The head ranges the runtime clamps to are *tighter*
than the ones the policy was trained with, and upstream's README calls head control "very
experimental" and warns it can break the head. And `Open_Duck_Mini_Runtime` carries no
LICENSE file, so all rights are reserved by its authors, while the design repository holding
the walk policy is Apache-2.0.

## Decision

- **The verb set is a strict subset, and the missing verbs are never declared.** `kick`,
  `grab`, `sit`, `stand` and `stand_up` are not gated off, they do not exist for this robot,
  which is what ADR-0017 already means by a manifest. The one place this needed help was
  `approach_and`, whose core description offers "kick, grab": this robot supplies its own
  wording naming `say`, `quack`, `express` and `observe`. A duck built without a camera or a
  speaker loses exactly the verbs `REQUIREMENTS` ties to them, with no branching of ours.
- **quackd ships a daemon that runs on the robot, and this is the first time.** Every other
  adapter talks to someone else's daemon. Here there is none, so `bridge/open_duck/` holds
  one. It does not reimplement the control loop: it rebinds the class upstream imports to
  read a gamepad, before that module executes, and then runs upstream's own script. The
  alternatives were rejected for reasons that compound: reimplementing the loop means
  transcribing an unlicensed repository *and* re-deriving an observation layout documented
  nowhere but in its source, and a second process means two owners of a serial bus that
  admits exactly one.
- **Going limp is unreachable rather than forbidden.** The only channel from the network to
  the body is seven floats and a few buttons, so no message can reach a torque register and
  there is no method to refuse. That is a stronger guarantee than "quackd never sends
  `robot.relax`", and it is the reason the shim was preferred even setting licensing aside.
- **The deadman is quackd's, it lives on the robot, and it is evaluated by the consumer.**
  Nothing upstream zeroes a command on silence, because a local pad is never silent. The
  daemon zeroes the three velocities after 300 ms inside the call the control loop makes
  every tick, not from a timer, so a server thread that is starved, wedged or dead still
  stops the duck. It holds the head rather than zeroing it: a velocity dropping to zero is
  what releasing a stick does and the policy has seen it, a neck snapping to centre is not.
  The manifest therefore says `deadman: true` with `native: none`, because the authority is
  our code, not a robot feature.
- **Head control is off unless the daemon is started with it on**, and then clamped to 80
  percent of the runtime's own range and rate limited to 1 rad/s, applied per elapsed
  second inside the control loop. (Until 0.7 that limit was applied per *received message*
  and scaled by the deadman window, which is not a rate: one `gaze` moved the head 0.3 rad
  and stopped, while a 10 Hz sender got 3 rad/s.) The range is not what
  breaks a neck linkage; a step command arriving from a network at 10 Hz is. Whether the
  head slots do anything at all without upstream's mode button is UNVERIFIED, and quackd
  will not press that button to find out.
- **The bridge protocol is quackd's own and is not in `upstream_api.py`.** It deliberately
  does not reuse the Microduck's `robot.move` and `robot.health`, which are that robot's
  `duck-ipc-proto` API v16: the same words on a different body, with different limits and no
  skills behind them, would be a false claim, and a transcript line saying `robot.move`
  would no longer identify which robot moved. Because quackd defines both ends there is no
  upstream to verify it against, so citing it as an upstream ref would be a false citation.
  What `upstream_api.py` carries instead is every assumption the daemon makes about the
  robot, UNVERIFIED and named.
- **`say` degrades to a sound, the way ADR-0023 decided for the Reachy.** There is no text
  to speech anywhere in the runtime. The text is logged verbatim and voiced as the closest
  of the duck's own sounds, and `extras.speech` is `"sounds"` so the prompt says the robot
  cannot pronounce words. The mood vocabulary and the antenna gestures are quackd's own,
  not upstream names, because the `.wav` files vary per build and upstream exposes servo
  positions rather than named gestures. Both are UNVERIFIED by construction and say so.
- **A fall is terminal, and every layer says so.** There is no recovery policy, so the
  precondition's message names no verb and tells a human to stand the duck up, the starter
  tasks tell the model to stop rather than thrash, and `stand_up` is not declared.
- **Nothing upstream is vendored.** Not the runtime, whose licence does not permit it, and
  not the Apache-2.0 walk policy, CAD or sounds, which the repo's standing rule keeps
  upstream anyway. The owner installs the runtime and fetches the policy themselves.
- **The `bridge` backend is EXPERIMENTAL**, with the same label as `microduck:jsonrpc`. It
  binds loopback by default and wants a token, because a port that walks a robot on a shared
  network is a hazard the other adapters do not have.

## Consequences

- quackd now has an on-robot artifact with its own version and its own way to be out of date
  relative to the laptop, so the handshake carries both and refuses a mismatch.
- Because the daemon is standard library plus numpy, the protocol is exercised end to end in
  CI against the real daemon over loopback. "The protocol works" is a fact; "the duck walked"
  stays a claim nobody has earned. The `bridge` row is 🧪 until someone runs it on a duck.
- A "battery below N%" abort cannot fire on this robot, because nothing reports a battery.
  `quackd validate` already warns.
- `search_scan` turns the whole body here rather than sweeping the head, because
  `scan_mode()` picks turning for any mobile robot with a twist. That is the right default
  on a robot whose neck moves about 23 degrees, and a test pins it so it stays a decision.
- Flock mode does not know this robot yet and says so; extending it is out of scope.
- If upstream renames the class the shim rebinds, the daemon refuses to serve rather than
  leaving a socket that controls nothing while a real gamepad drives the duck.
