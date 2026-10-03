# Design: quackd 0.5, the first robot you can actually build

**Status:** implemented in 0.5.0 · **Branch:** `feat/open-duck-mini` · **Shipped:** 2026-09-03

## Why

quackd 0.4 calls itself a brain for any small robot and ships four adapters, and not one of
them can be run against real hardware by an outsider. `microduck:jsonrpc` waits on a robot
that ships around Christmas 2026. `reachy_mini:sdk`, `lerobot:real` and `rosbridge:ws` are
all "verified names, never run against the real thing". Everything that works, works in the
simulator.

The [Open Duck Mini v2](https://github.com/apirrone/Open_Duck_Mini) is the exception. It is
open hardware, roughly EUR 350 to 414 in parts, and people are printing and assembling them
at home right now. If quackd is going to be a brain for a small robot, this is the robot a
stranger can put it on.

The thesis does not change: the LLM picks verbs, the robot's own controllers move, quackd
enforces the contract. What changes is that for one body, quackd also ships the process that
hosts those controllers, because that robot has nothing to talk to otherwise.

## What made this robot different

Every previous adapter connected to somebody else's daemon: `robotd`, `reachy-mini-daemon`,
`rosbridge_server`, a LeRobot host process. The Open Duck Mini has none. Its runtime runs a
50 Hz ONNX walk policy driven by a **local pygame gamepad**, and its only socket checks the
IMU. Reading upstream's 34 Python files at `3203734` confirmed there is no control server of
any kind.

It also has less than the Microduck: no beak, no gripper, no kick policy, no sit policy, no
battery readout, and no get-up-after-fall policy. That last one shapes every task written
for it, because a fallen duck is a job for a human.

Two upstream facts settled the licence question. `Open_Duck_Mini` (the design, the docs and
the walk policy) is Apache-2.0. `Open_Duck_Mini_Runtime`, which holds the package the bridge
needs, has **no LICENSE file at all**, so all rights are reserved. quackd may cite its names
as facts and import what an owner installed themselves, and may not copy a line of it.

## The decisions

Recorded in [ADR-0024](../adr/0024-open-duck-mini.md); the short version:

| Decision | Why |
|---|---|
| The missing verbs are never declared | ADR-0017 already means this: a verb absent from a manifest does not exist in the registry, MCP, validation or the prompt. `kick`, `grab`, `sit`, `stand` and `stand_up` needed no new gating, only silence |
| The bridge replaces the loop's *input device*, not the loop | Reimplementing it means transcribing an unlicensed repository and re-deriving an observation layout documented nowhere but in its source. Rebinding the class upstream imports keeps one process, one owner of the serial bus, and nothing copied |
| Going limp is unreachable, not forbidden | The only channel from network to body is seven floats and a few buttons, so there is no method that touches torque and none to refuse. Stronger than a promise not to send one |
| The deadman is ours, runs on the robot, and lives in the consumer | Nothing upstream zeroes on silence, because a local pad is never silent. Evaluating it inside the control loop's own call means a starved, wedged or dead server thread still stops the duck |
| Head control is off by default | Upstream warns it can break the head. On, it is clamped to 80 percent of upstream's range and rate limited, because a step command over a network is what breaks a neck, not the range |
| The camera is a second process | Encoding a 512 by 512 JPEG inside a 20 ms tick is not affordable on a Pi Zero 2 W, and picamzero in the walk process costs tens of megabytes on a 512 MB board |
| The protocol is quackd's own | Reusing the Microduck's `robot.move` would be a false claim about a different body, and would make a transcript ambiguous about which robot moved |

## What the work found

Building it was the easy half. Auditing the documentation before release turned up that the
path we were about to publish could not be walked, and that some of what the project said
about itself had quietly stopped being true.

- The bridge's own installer wrote an auth token and **the client had no way to send one**.
  Anyone following the instructions was refused at the handshake.
- `quackd doctor --address` was written in four documents and was not a flag. Worse, nothing
  in quackd could show what a connected robot reported, because `doctor` and `list-verbs`
  both read the static manifest.
- `open-duck-lookout`, the task four documents said to run first *because* it moves no legs,
  was the only task that required the one flag every document calls dangerous.
- The camera server bound `0.0.0.0` by default, with no authentication, and no document said so.
- Perception was attached only for `sim2d`, so **every** hardware backend with a camera ran
  blind. That was a 0.4 bug this release inherited and fixed for all of them.
- "quackd never touches this layer on any body" was in three places and had become false.

Each is fixed or, where it is not fixed, written down. `say` on hardware reaches the pad's
random-sound button, so the mood quackd picks selects nothing, and the docs say that rather
than describing the intent.

## Acceptance

- `open-duck-scout` on `open_duck:sim2d`, 10 of 10 seeds, ground truth checked.
- `open-duck-lookout` never leaves its starting position, asserted.
- The real daemon and the real client drive each other over loopback in CI, including the
  camera server, so the whole chain except the duck is exercised.
- The daemon imports on a machine where every quackd dependency is poisoned, which is what
  makes `pip install --no-deps quackd` viable on a 512 MB board.

## Only a human can do these

Run `open_duck:bridge` against a duck they built, working through
[the checklist](../adapters/open_duck/hardware-checklist.md), and confirm the deadman by pulling the
laptop's Wi-Fi mid-walk. Until then the `bridge` row stays 🧪, which is the same rule every
other adapter lives under.
