# ADR-0028: ToddlerBot: quackd ships the loop, and everything upstream forgot to protect

**Status:** accepted · **Date:** 2026-09-05 · Extends ADR-0017, ADR-0022 · Follows ADR-0024 · Implemented in 0.7 ([page](../adapters/toddlerbot/README.md))

## Context

ToddlerBot is a small open source humanoid: about 56 cm and 3 kg, two arms, two legs, a two
joint neck, thirty Dynamixel servos on a communication board, a Jetson on its back. It is
quackd's first full humanoid, which makes it the Open Duck and the LeRobot arm on one body.

The pin is the commit the annotated tag `v2.0.0` points at, `84e02d1`, chosen deliberately:
the request was for ToddlerBot 2.0 and this is 2.0. That choice was made with its costs on the
table and is recorded below.

Read from upstream source at that commit. The robot verifiably has a six method `BaseSim`
contract, a fifty hertz control period, five constructible builds of thirty to thirty-two
motors, a camera, a speaker, an IMU, and eighteen keyframe motions. It verifiably does **not**
have a network API, a watchdog, a timeout, an e-stop, a reset, a safe pose, a fall detector, a
battery readout in Python, a text to speech, a signal handler, or a walk policy that ships with
the code.

Three facts decided the whole design, and all three are upstream's own state.

**There is nothing to talk to.** No socket, no daemon, no IPC: a Python library whose control
loop opens serial ports in-process.

**Silence means hold forever, not stop.** `RealWorld.step()` is a no-op, so nothing times out
and nothing re-arms. On a Microduck, going quiet is the safe action. Here it is the dangerous
one.

**The normal way to exit drops the robot.** A C level `atexit` handler disconnects every open
client, and disconnecting disables torque. Any unhandled exception, any Ctrl-C, any plain
return from `main` de-torques a standing humanoid with no lowering and no ramp. And `SIGTERM`
does not even reach that handler, because C `atexit` does not run on a signal and upstream
installs no Python handler anywhere, so systemd stopping the process leaves the robot fully
torqued holding its last target instead.

## Decision

- **quackd ships the daemon, and it owns the loop.** `bridge/toddlerbot/` runs the fifty hertz
  loop continuously; quackd's intents steer what it is already doing. This is not only because
  there is no API to speak to, which would be the ADR-0024 argument on its own. It is because
  a verb is episodic and this body is not: a humanoid frozen mid-stride while a model thinks is
  a humanoid on the floor.
- **The daemon carries seven mechanisms upstream has not got**, each one because a specific
  read said so: a clamp against the joint limits, because `set_motor_target` clamps nothing and
  the motors are in multi-turn mode with the firmware limits off; a per-tick rate limit,
  because a position command here is a full-torque snap; an all-zeros detector, because a
  failed bulk read returns a zeroed buffer indistinguishable from every joint at zero; a
  `KeyError` guard, because a controller fault arrives that way rather than as an exception; a
  safe-pose slew, because no reset exists anywhere; signal handlers that settle first; and a
  construction watchdog, because the constructor busy-waits forever on a silent IMU with the
  motors already live.
- **Shutdown settles before it closes, and closing is on a deadline.** `close()` is bound
  without releasing the GIL and retries torque-off forever on an unresponsive bus, so a stuck
  shutdown freezes every thread that might have supervised it. The daemon reaches the safe pose
  first, then arms a hard-exit timer and calls `close()`. A torqued robot and a dead process is
  worse than a clean shutdown and better than a frozen process nobody can signal.
- **`safety_authority` is `native: none`, and `deadman` is true wherever something is
  actually running one.** There is no watchdog, no timeout, no e-stop, no reset and no
  current limit anywhere upstream, and the motors are in multi-turn mode so the
  firmware's own position limits are off too. The robot is therefore not a safety
  authority in any sense the manifest can name, and `native` says so. The only
  deadman that can exist is one quackd runs, and each backend answers for itself:
  `mock` and `sim2d` emulate it and say true, and `:bridge` says **false** until the
  daemon has actually answered the handshake, because before that there is nothing on
  the other end to be running anything. Connecting is what turns it true. `heartbeat_hz` is 5, an order of magnitude
  below the control rate, because quackd's heartbeat only has to notice that the
  daemon stopped answering: the fifty hertz loop and its own deadman are the
  daemon's job. `extras.deadman_scope` says what tripping it actually does, which is
  slew to a safe pose and hold, never go limp.
- **The deadman is a trajectory, not a message.** On a duck it is one word, because zero
  velocity is a safe state. Here the command is an absolute pose, so the three things available
  at the hardware boundary are hold the last target, jump to a new one, and go limp, and none of
  them is safe by default. So the deadman slews to upstream's own default pose at upstream's own
  0.3 rad/s, waist first as upstream's own reset does, and holds. It takes seconds, which is why
  it cannot live in a signal handler or an `atexit` and has to live in the loop.
- **`stop` means hold the last verified-good measured pose.** `stop` is a mandatory core verb
  that is always safe and never gated, and on this body it cannot mean zero velocity, because
  there is no velocity at the hardware boundary. It also cannot mean a fresh read, which may be
  the all-zeros buffer. So the daemon keeps a last-good cache and `stop` holds that.
- **Locomotion does not exist unless a checkpoint is staged.** The gait is an ONNX artifact from
  a wandb run that upstream neither publishes nor checks in, so on a bare install there is no
  walk at all. `move`, `go_to` and `approach_and` all need the twist intent, so rather than
  gating them behind a precondition the manifest simply does not declare them and `mobility` is
  `none`. This is the ADR-0017 rule applied at its strictest, and it is decided at connect from
  what the daemon reports rather than from configuration.
- **`say` is absent for good, and so is a battery.** The speaker plays audio and nothing at this
  pin synthesises speech, so the `sound` intent is not declared. Bus voltage is read in C++ and
  only printed, so `battery_percent` is `None` and a battery abort can never fire.
- **A fall is terminal and every layer says so.** There is no recovery policy, so `stand_up` is
  not declared, the precondition names no verb and tells a human to stand the robot up, and the
  starter task tells the model to stop rather than thrash. This is the Open Duck precedent, on a
  body four times the mass.
- **No raw joint verb.** The `joint` intent is not declared. Thirty unclamped radians from a
  language model, on a machine in multi-turn mode with no current limit and no position limit,
  is the exact failure this project exists to prevent. quackd offers named moves and the daemon
  owns every trajectory.
- **quackd curates the motion list, and says that it is curating.** Five of the nine shipped
  motions are offered. The other four are excluded on judgement rather than capability, which
  is a different thing from the absences above and is documented as such: two assume the robot
  is hanging from a bar, one is a gait reference, and one is a cartwheel on a body with no fall
  recovery.
- **Nothing from `descriptions/` is ever vendored.** The code is MIT but the design is CC
  BY-NC-SA 4.0, which is non-commercial, and the meshes, MJCF and URDF fall under it. The owner
  supplies their own checkout, including for any CI fixture.

## Consequences

- The `v2.0.0` pin costs a torque-on binding: `enable_motors` exists in C++ and is not bound to
  Python at this commit, so there is no way back from a torque-off short of `initialize`, which
  also rewrites every gain and re-latches the zero positions. quackd therefore never disables
  torque, and `relax` is not declared at all. That is stricter than the house rule against
  sending a go-limp call, and it costs nothing here.
- The pin also means upstream's own CI never ran: the workflow echoes a skip and exits zero,
  and the repository has one test file. quackd assumes nothing works until it has run it.
- The daemon and the protocol are exercised end to end against a fake body over a real loopback
  socket, on every CI platform, including the deadman, the settle, the clamp, the rate limit,
  the all-zeros detector and the controller-fault path. "The machinery works" is a fact; "the
  robot stood up" stays a claim nobody has earned, and the `bridge` row is 🧪.
- quackd now has a third on-robot artifact (after the Open Duck Mini's pair of daemons and
  the AlohaMini's host wrapper), with its own version and its own way to be out of
  date relative to the laptop, so the handshake carries both and refuses a mismatch.
- `search_scan` sweeps the head here rather than turning the body, which is the opposite of the
  Open Duck's choice and for the same reason in reverse: turning a humanoid with no fall
  recovery to look around is not the first thing to reach for.
- Flock mode does not know this robot, and extending it stays out of scope.
  Amended 2026-09-13 by [ADR-0034](0034-registered-robots-and-pilot-flocks.md): true of
  the *coordinator* flock, whose runner is still Microduck-only. A **pilot** flock takes any
  adapter and backend, so this robot can be a member of one today.
- Eight unknowns remain and are named in `upstream_api.py` as UNVERIFIED, the largest being
  whether the safe pose is safe to slew to from a crawling or prone start. Only a robot on a
  stand retires that one.
