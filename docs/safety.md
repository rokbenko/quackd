# Safety

A biped falls in 0.3 s; an LLM answers in 3 s. Everything here follows from that.

## Layers

| Layer | Owner | What it guarantees |
|---|---|---|
| Body | the robot's own controller | **Whatever that particular body actually offers, which is not the same everywhere.** The Microduck's `robotd` gives joint and thermal clamps, fall detection and a **deadman**: velocity goes to zero when `robot.move` notifications stop. An Open Duck Mini v2 gives *none of those* — no fall detection, no thermal clamp, no deadman of its own (its command source is a local gamepad, which is never silent), and no way to get up if it goes over; its deadman is quackd's own daemon on the Pi, and the human watching is the only fall detector. The body is still the sole safety authority: clients send intents, never motor writes. What each body offers is declared in its manifest's `safety_authority`, and `quackd doctor` prints what the robot itself reported (see "On other bodies"). |
| Conversation | quackd `Executor` | The LLM and MCP clients can only do what the `.duck` allows, as often as the budget allows, with a human in the loop where the contract says so. |
| Session | quackd `Heartbeat` + `KillSwitch` | A dead transport or a worried human ends in a `stop` intent. |

## The executor (mirrors upstream's own rules)

Every verb call — from the agent loop or an MCP session — passes `Executor.run_verb`, in
this order: abort flag → **allowlist** (`verbs.allow`; `stop` always allowed) → param
validation (errors are feedback to the model, not crashes) → **confirm gate**
(`verbs.confirm` or `safety_class` ∈ {confirm, dangerous}; y/N in the terminal, `--yes` to
auto-accept, MCP refuses unless `--yes`) → **budgets** (`max_steps` here; `max_llm_calls`
and `max_minutes` in the loop) → machine-enforced **`abort_when`** (battery threshold,
consecutive failures) → **preconditions** (not fallen, not sitting) → `--dry-run` → execute
with a **timeout**. A verb that times out or raises stops the duck and reports a failure.

## Heartbeat

A task pings `transport.heartbeat()` every 500 ms (`robot.health` on hardware, a liveness
check in sim). One failure → `stop` intent → abort flag → the loop ends with
`outcome: aborted`. Upstream's own rationale: "LLMs stall mid-inference".

## Kill switch

Ctrl-C and `q` (when stdin is a terminal) set the same abort flag; the loop's `finally`
always sends `stop` and closes the transport. Works on Windows (signal handler, not
`loop.add_signal_handler`).

## Dry run

`--dry-run` prints every intent a model *would* send and sends nothing. Read-only verbs
(`observe`, alias `get_frame`, and `report_state`) still run. Use it the first time you
point a new `.duck` at hardware.

## On hardware

Nothing here has run on hardware yet, on any body. When it does, start with `--dry-run`
every time, then a `.duck` whose `allow` list is the smallest thing that could work, then
widen it. **You are responsible for your robot.**

**A Microduck (a 25 cm biped):**

- **Run on the floor, not a table.** A 25 cm biped and a table edge do not mix.
- **Keep pets and kids clear of `kick`** (and `grab`, and `roulade`).
- **The gamepad preempts remote control.** Upstream arbitrates authority; there is no
  stop button because releasing the sticks stops the robot via the deadman. quackd does not
  try to out-rank the pad.
- quackd never sends `robot.relax` (torque off — the robot collapses) or `robot.init`
  (moves every joint). Use `robotctl` for those, with the robot on its stand.
- A good first contract: `allow: [quack, gaze, stop]`, then add walking.

**A Reachy Mini (a head that exists today):**

- `wake_up` moves every joint, which is why it is confirm-gated in the manifest. Give the
  head clearance before allowing it, and keep fingers away from the neck linkage.
- There is no deadman and no e-stop that we verified, so quackd's heartbeat and `stop`
  (which is `cancel_move`) are the only thing that halts a move in progress.
- The head has no battery reading, so a `Battery below N%` abort cannot fire on it.

**A LeRobot arm (an SO-101 class arm on a desk):**

- An arm sweeps a volume. Clear it before `move_joints`, and keep hands out of the path.
  A gripper is a pinch hazard even at the 50 % torque cap LeRobot writes at `configure()`.
- `pick` hands the whole arm to a learned policy for up to a minute. It is confirm-gated
  for that reason. Watch it, and keep `stop` within reach.
- `stop` holds position, it does not release. LeRobot's own `disconnect()` releases torque
  at the end of a session by its default, so the arm can sag when the run ends: do not
  leave it holding something fragile.
- Calibration is interactive and quackd never triggers it. An uncalibrated arm is refused.

**A wheeled base over rosbridge:**

- No deadman was verified anywhere in that stack, so if quackd dies mid-verb the base
  keeps its last Twist until its own driver times out, if it does at all. Test on blocks
  with the wheels off the ground before testing on the floor.
- quackd re-sends the Twist at 10 Hz while a verb runs and publishes a zero Twist on
  `stop`, on close, and when the heartbeat fails. That is the entire stop authority.
- The speed limits are quackd's caution (`limits.max_vx`, `max_wz` in the manifest), not
  the base's capability. Lower them before the first real drive.

**An Open Duck Mini v2:**

- **If it falls, quackd cannot pick it up.** There is no get-up policy on this robot, so
  `stand_up` does not exist for it and every verb that moves it refuses until a human
  stands it up. Work with the duck on a stand until you trust the link.
- The deadman is quackd's own, and it runs on the robot. quackd's bridge daemon zeroes the
  velocity after 300 ms of silence, inside the call the control loop makes every tick, so a
  server thread that is starved, wedged or dead still stops the duck. Test it by pulling
  your laptop's Wi-Fi mid-walk before you rely on it.
- Going limp is unreachable rather than forbidden: the only channel from the network to the
  body is seven floats and a few buttons, so no message reaches a torque register.
- Head control is off unless you start the daemon with it on, and then it is clamped inside
  the runtime's own range and rate limited. Upstream warns that head control can break the
  head, and quackd never presses its mode button.
- The Feetech serial bus has exactly one owner. The bridge *is* the walk loop, so do not run
  it and upstream's script at the same time.
- The bridge binds loopback and wants a token, because a port that walks a robot on a shared
  network is a hazard. Prefer `ssh -L 9871:127.0.0.1:9871 your-pi`.
- The camera is a second process (`quackd_duck_camd.py`) serving one JPEG over HTTP with
  **no authentication at all**. It binds loopback and warns if you bind it wider, because
  it shows whatever the robot can see. Tunnel it rather than exposing it.
- The only e-stop is the power switch.
- The order to bring one up in, feet off the ground until step 10, with an abort condition
  at every step: [open-duck-hardware-checklist.md](open-duck-hardware-checklist.md).

## On other bodies

Since 0.4 quackd drives more than the duck, and the honest answer to "what stops it when
quackd goes quiet" differs per body. Each manifest says so
(`safety_authority: {native, deadman}`), and `stop` always means stop, never collapse:

| Body | Native authority | What `stop` does | Never sent |
|---|---|---|---|
| Microduck (`microduck:*`) | `robotd_deadman`: velocity zeroes when intents stop | `robot.stop` | `robot.relax`, `robot.init` |
| Reachy Mini (`reachy_mini:*`) | `none`: no client deadman or e-stop was verified; quackd's heartbeat is the authority | `cancel_move` | `disable_motors` (limp) |
| LeRobot arm (`lerobot:*`) | `torque_limit`: the gripper's torque and current caps, plus `max_relative_target` when configured; no deadman, a position-controlled arm holds its goal | re-sends the present position as the goal (hold) | `disable_torque` (LeRobot's own `disconnect()` does, by its default, at the end of a session) |
| rosbridge base (`rosbridge:*`) | `none`: neither rosbridge nor the driver has a deadman we verified | publishes a zero Twist; quackd also re-sends the Twist at 10 Hz while a verb runs | silence |
| Open Duck Mini v2 (`open_duck:*`) | `none` in the robot, but quackd's own bridge daemon runs on it and zeroes the velocity after 300 ms of silence, inside the 50 Hz loop | zero velocity, head held, torque still on | anything that reaches torque, the head-control mode button, any direct servo or IMU read |

The verbs a body lacks are not gated, they do not exist: a head cannot `kick`, an arm
cannot `move`, a base cannot `say`, and `validate --robot` says so before a run starts.
`pick` on the arm and `wake_up` on the head are confirm-gated in their manifests because
they move the whole body under a controller quackd does not write.

## What quackd does not protect against

A model that is *allowed* to `walk` can walk into a wall; the sim has walls, your living
room has stairs. The allowlist is your tool: a `.duck` for a new space should start small.

Since 0.6 there is one more thing to know about. A robot's memory
([memory.md](memory.md)) is text a model wrote, kept on disk, and handed to the *next*
model as part of its system prompt. The executor never reads it, so a note cannot widen an
allowlist, lift a budget or open a confirm gate: none of the guarantees above depend on it
being true. What a note can do is persuade a later run, including a later run of a different
task on the same body. A model that concludes something wrong ("the sofa is safe to walk
under") will keep telling itself so until somebody deletes the line. That is the whole point
of the feature and also its whole risk, which is why the file is plain text you can read,
`quackd memory show` prints exactly what the pilot was told, `quackd memory clear` forgets
it, and `--no-memory` runs as if it were never there.

Report anything that lets a model bypass the executor — see [`SECURITY.md`](../SECURITY.md).
