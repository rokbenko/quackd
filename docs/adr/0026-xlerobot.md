# ADR-0026: XLeRobot: quackd speaks the wire, because there is no package to import

**Status:** accepted · **Date:** 2026-09-04 · Extends ADR-0017, ADR-0022 · Contrasts with ADR-0024 · Implemented in 0.7 ([page](../adapters/xlerobot/README.md))

## Context

The XLeRobot is a dual-arm mobile manipulator built from LeRobot-ecosystem parts bolted to an
IKEA RÅSKOG cart: two five-joint arms with grippers, a two-motor head, and a three-omniwheel
holonomic base, seventeen Feetech STS3215 servos in all, Apache-2.0, around $660 in parts. It
is the second body quackd supports that a stranger can actually build, and the first with both
a mobile base and arms — every adapter before it was one or the other.

Read from upstream source on 2026-09-04, at `Vector-Wangel/XLeRobot` commit `3d14695`
(`main`, 2026-07-22; the repository has zero tags and zero releases, so a hash is the only pin
available). The robot verifiably has: a ZeroMQ host binding PULL on 5555 and PUSH on 5556 with
`CONFLATE` on both, a flat JSON action and observation dict of seventeen dotted float keys, a
30 Hz publish loop, a 500 ms watchdog that calls `stop_base()`, and a one-hour self-terminating
session. It verifiably does **not** have a speaker, a microphone, an IMU, a lidar, odometry, a
battery data link, a documented head axis mapping, inverse kinematics, or a shipped policy.

Two further facts shaped everything. **XLeRobot is not an installable package** — no PyPI
entry, no `pyproject.toml`, no `setup.py`, absent from upstream `huggingface/lerobot` — and its
documented install is copying files into an existing lerobot source tree. And a stock cart is
**blind**: `xlerobot_cameras_config` returns a dict with every entry commented out.

## Decision

- **quackd speaks the ZeroMQ host protocol and imports nothing.** There is no dependency that
  expresses "copy these files into your site-packages", so importing was never available. The
  extra is `pyzmq` and nothing else, which keeps quackd's 3.11 floor and works on Windows,
  unlike `quackd[lerobot]`, which is gated behind 3.12 and pulls torch. Reusing `lerobot:real`
  was rejected because it refuses any `robot_type` that is not an SO-101 follower and declares
  `mobility="none"`, which would structurally delete every verb a mobile base exists for.
- **This is the mirror image of ADR-0024, and the reasoning is the same one.** The Open Duck
  Mini needed quackd to ship a daemon because its runtime had no network API at all. This robot
  ships one. Writing a second daemon here would be roughly three times the work for nothing, so
  the rule is not "quackd ships daemons", it is "quackd uses the robot's own control channel
  when there is one".
- **The client holds one desired action and re-sends the whole of it.** `CONFLATE` keeps only
  the newest message, so a client sending one message per intent silently loses the first of
  any two in a host cycle. Holding the merged action means a dropped message is recovered by
  the next one rather than lost. A loopback test fires two intents into one cycle and asserts
  both landed.
- **A command that carries no velocity keys zeroes the base, and quackd reproduces that rather
  than papering over it.** Upstream calls `_body_to_wheel_raw` unconditionally with
  `.get(key, 0.0)` defaults, so an arms-only action stops the wheels. Hiding that would mean
  keeping the cart driving while an arm moves, which is worse. So a `joint` or `gripper` intent
  explicitly zeroes the desired velocity, the mock does the same, and `move_joints` reports
  `base_stopped` in its result.
- **Stop zeroes the wheels and leaves every arm goal exactly where it was.** It deliberately
  does not rebuild a hold from the latest observation: that reading can be several cycles
  behind, nothing on the wire is timestamped, and sending a stale position to a servo does not
  hold an arm, it moves one. The goals already in the desired action are what quackd last asked
  for, so leaving them alone is the real hold. quackd never sends upstream's `disconnect()`,
  which disables torque and drops whatever is held.
- **`say` and `gaze` are not declared, and neither is a battery.** There is no speaker and no
  microphone in the bill of materials and no audio code in the repository, so the `sound`
  intent does not exist here and `say` disappears by `REQUIREMENTS` rather than by a branch of
  ours. The head's two motors are on the wire, but which is yaw and which is pitch is stated
  nowhere upstream; the best available evidence is second-hand, from upstream's own agent
  library, and its pitch range is not centred on zero. So quackd never commands the head at
  all, and `search_scan` turns the whole cart. The power station has no data link, so
  `battery_percent` is permanently `None`.
- **The manifest is built from what the first observation actually carried, not from config.**
  A stock cart is blind, so `observe`, `go_to`, `search_scan` and `approach_and` exist only
  once a camera has been seen on the wire. A camera is identified as any key whose value is a
  string, because the seventeen state keys are floats and the host writes cameras as base64.
- **`safety_authority` is `native: none` with `deadman: true`, scoped in `extras`.** The 500 ms
  watchdog is real and it is the reason this topology was chosen over driving the serial buses
  directly, where a killed process leaves a cart driving. But it calls `stop_base()`, which
  zeroes three wheels and nothing else, so `deadman_scope` says `base_only` rather than letting
  the flag imply more than it covers.
- **`limits` narrow to the schema, not to the robot.** The cart's own fast tier is 0.3 m/s and
  90 deg/s, but `MoveParams` caps `vy` at 0.2 and `wz` at 1.5 rad/s and `limits` may only
  narrow. On the two bases that cannot strafe, `max_vy` is `0.0`, which is how "this variant
  cannot slide sideways" is said in a manifest: the request is clamped and reported instead of
  being accepted and silently ignored by the robot.
- **`pyzmq` joins the `dev` extra.** CI installs only `dev`, and the fake-host test is what the
  `zmq` row's 🧪 rests on, so a status claim CI cannot check would not be honest. It is a small
  pure wheel on every platform the matrix runs, unlike torch or onnxruntime.

## Consequences

- The wire is exercised end to end against a fake host quackd wrote from upstream's source at
  the pin, on real loopback sockets, on every CI platform including Windows. "The protocol
  works as we read it" is a fact; "the cart moved" stays a claim nobody has earned, and the
  `zmq` row is 🧪 until someone runs it.
- That fake caught a real bug before any hardware could: `stop` was rebuilding its hold from
  the latest observation, which had not caught up, so stopping would have commanded an arm back
  to zero. A stop that moves an arm is exactly the failure this project exists to prevent.
- quackd now depends on reading upstream correctly rather than on upstream's own client, so
  every wire fact is a VERIFIED `UpstreamRef` with a pinned line, and the eight assumptions
  that remain are UNVERIFIED and named.
- `xlerobot-lookout` moves no wheel and no arm, which makes it the safe first task for a real
  cart. It is also the thinnest starter quackd ships, because without `gaze` and without `say`
  a task that moves nothing can only look and report.
- The host is commented out of upstream's own package `__init__` and exits after an hour, so
  the common failure is a socket that stops answering. quackd's heartbeat names it and says
  what to restart, rather than hanging.
- Flock mode does not know this robot, and extending it is out of scope, as it was for the
  Open Duck Mini.
  Amended 2026-09-13 by [ADR-0034](0034-registered-robots-and-pilot-flocks.md): true of
  the *coordinator* flock, whose runner is still Microduck-only. A **pilot** flock takes any
  adapter and backend, so this robot can be a member of one today.
