# ADR-0027: AlohaMini: quackd speaks the wire again, and ships the host that switches the arms on

**Status:** accepted · **Date:** 2026-09-05 · Extends ADR-0017, ADR-0022 · Follows ADR-0026 · Contrasts with ADR-0024 · Implemented in 0.7 ([page](../adapters/alohamini/README.md))

## Context

The AlohaMini is two follower arms on a motorised vertical lift, on a three-omniwheel
holonomic base. Despite the name it is architecturally a LeKiwi descendant rather than an
ALOHA one, which its own bill of materials says outright. It is quackd's second bimanual body
and its first with a vertical axis.

Read from upstream source on 2026-09-05, at `liyiteng/lerobot_alohamini` commit `ab4462b`
(`main`, 2026-07-24; no tags, no releases). The robot verifiably has: a ZeroMQ host binding
PULL on 5555 and ROUTER on 5556, a request/reply observation channel returning JSON plus JPEG
multipart frames, a 30 Hz loop, a 1 s watchdog, a 6000 s lifetime, three SKUs with six or seven
joints per arm, and a lift with soft limits at 0 and 600 mm and a 5 mm descent floor. It
verifiably does **not** have a speaker, a head, odometry, a battery readout, or arm torque.

Three facts shaped everything, and all three are upstream states rather than opinions.

**The software is a fork of LeRobot that calls itself `lerobot`.** It is not on PyPI, installs
only from a large git clone on Python 3.12 with torch, and its distribution name collides with
HuggingFace's own package. There is no way to express it as a dependency.

**The arms have no torque.** `configure()` disables it on both buses and both `enable_torque()`
calls are commented out. Nothing else in the package enables it: the only `Torque_Enable` write
anywhere is a zero inside `lift_axis.home()`, and neither `configure_motors` nor
`write_calibration` touches it.

**The lift latches, and its two action keys are not symmetric.** `apply_action` is two
independent `if key in action` blocks with no `else`. Neither key means no write at all, so the
servo keeps travelling while the command refreshes the watchdog that would have stopped it.
Both keys means the velocity branch runs second and wins. And `home()`, which `connect()` calls
on every calibrated robot, leaves full-speed descent in that register because the write that
would zero it is commented out.

## Decision

- **quackd speaks the ZeroMQ host protocol and imports nothing**, for the same reason as
  ADR-0026 and a stronger one: there is no importable package here at all. The extra is
  `pyzmq`, shared with `xlerobot`.
- **Speaking the wire is also the more correct choice, not merely the available one.**
  Upstream's own client discards the host's `_images` list and trusts its own camera config,
  and its `robot_model` default is `alohamini1` while the host's is `alohamini2` with nothing
  cross-checking them. Reading the wire makes both mismatches impossible rather than silent, so
  the SKU and the camera set are derived from the observed keys and never from configuration.
- **One function builds every payload, and it enforces two invariants.** All three velocity
  keys are always present, because `send_action` indexes them with no default and would
  otherwise discard the whole action, arms included, without refreshing the watchdog. And
  exactly one lift key is always present: the height while deliberately driving the lift, the
  velocity zero the rest of the time. Neither key would leave the lift latched; both would
  freeze it. This is the single highest-value piece of defensive code in the adapter and it
  lives in one place so it cannot be got wrong twice.
- **The first thing quackd says to this robot is a stop**, because homing left it descending.
- **`stop` zeroes the wheels and the lift, repeats itself, and leaves every arm goal alone.**
  It repeats because `stop_base` writes with `num_retry=0` on a bus it shares with the lift. It
  leaves the arm goals alone because the goals already held are what quackd last asked for,
  while a hold rebuilt from the latest observation would be built from a reading several cycles
  old, and a stale position sent to a servo moves an arm rather than holding one. That lesson
  came from ADR-0026's own bring-up.
- **quackd ships a host wrapper, and this is the second time.** `bridge/alohamini/` holds a
  ~120-line wrapper that runs upstream's own host loop with arm torque enabled, the lift
  stopped, and three `quackd_` fields in every observation. It wraps rather than reimplements:
  upstream's loop reads the cameras, the currents and the over-current trip, and transcribing
  that would mean owning a copy that drifts. This is not ADR-0024's situation, where upstream
  had no network API at all; here the API exists and only the torque is missing.
- **`arm_torque` is a precondition, not an assumption.** Against a stock host the three arm
  verbs refuse with a message naming the fix. quackd asserts torque from a field its own
  wrapper puts on the wire, which is honest about what it actually knows, and the refusal is
  better than commanding joints that would not move.
- **`say` and `gaze` are not declared, and neither is a battery or odometry.** No speaker, no
  head, no battery readout, and an observation carrying velocities and no pose. Each absence is
  upstream's, and each one removes verbs through `REQUIREMENTS` rather than through a branch of
  ours.
- **`lift` re-sends its target while it travels.** The robot takes one proportional step per
  command received and its watchdog stops the lift after a second of silence, so a single
  command moves the lift a little and then the deadman ends it. This is the same reason `move`
  re-sends a velocity, and it was found by a mock faithful enough to model the watchdog.
- **`safety_authority` is `native: none` with `deadman: true`, scoped in `extras`.** The
  watchdog is real but calls `stop_motion()`, which is the base and the lift and never the arms,
  so `deadman_scope` says `base_and_lift_only` rather than letting the flag imply more.

## Consequences

- The wire is exercised end to end against a fake host quackd wrote from upstream's source, on
  real loopback sockets, on every CI platform. That fake reproduces the bugs deliberately, and
  it is what makes `test_stop_zeroes_the_lift` mean something: a naive three-key stop,
  which is the shape the driver invites, fails it.
- Two real bugs were caught before any hardware could see them. `get_state` polled for zero
  milliseconds on a request/reply wire, so it sent a request, gave up before a reply could
  arrive, and would have served a stale reading every single time. And `lift` sent once and let
  the watchdog end the travel.
- quackd now depends on reading upstream correctly rather than on upstream's own client, so
  every wire fact is a VERIFIED `UpstreamRef` with a pinned line and the nine assumptions that
  remain are UNVERIFIED and named.
- A second on-robot artifact exists, with its own version, and quackd can tell it from a stock
  host only by a field it adds itself. That is stated as UNVERIFIED rather than implied.
- `alohamini:sim2d` earns the simulator row because the shared world already integrates a
  holonomic base; the lift and the arms are added in memory on top, and the manifest already
  says `holding` is commanded rather than sensed, so nothing is claimed that is not true.
- Flock mode does not know this robot, and extending it stays out of scope.
  Amended 2026-09-13 by [ADR-0034](0034-registered-robots-and-pilot-flocks.md): true of
  the *coordinator* flock, whose runner is still Microduck-only. A **pilot** flock takes any
  adapter and backend, so this robot can be a member of one today.
