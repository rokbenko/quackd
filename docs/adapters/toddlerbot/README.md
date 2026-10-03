# ToddlerBot

A small open source humanoid, about 56 cm and 3 kg, with two arms, two legs, a two joint neck
and thirty Dynamixel servos. It is quackd's first full humanoid, and the third body whose
robot side quackd ships, because upstream has no network API of any kind.

Upstream: [hshi74/toddlerbot](https://github.com/hshi74/toddlerbot), pinned at
[`84e02d1`](https://github.com/hshi74/toddlerbot/tree/84e02d14261292eec5d06f896e3145b35c54856c),
which is the commit the annotated tag `v2.0.0` points at (read 2026-09-05). Note the tag
object's own sha, `6fde5df5`, is **not** a commit and pinning it gets a 422. Code is MIT; **the
design, including everything under `descriptions/`, is CC BY-NC-SA 4.0 and therefore
non-commercial**, so quackd vendors none of it.

**Nothing here has ever run on a robot.**

```bash
uv pip install 'quackd[toddlerbot]'

# offline
quackd run toddlerbot-lookout --robot toddlerbot:sim2d --llm fake

# a real robot, once quackd's daemon is running on it
quackd run toddlerbot-lookout --robot toddlerbot:bridge \
  --address tcp://toddlerbot.local:9873
```

That extra dates from 0.10.0, and it installs exactly one package: `quackd-toddlerbot`,
quackd's own Apache-2.0 adapter, whose only dependency is quackd itself. The client is stdlib,
and nothing of upstream's is in it, the non-commercial `descriptions/` included. Before 0.10.0
the adapter was part of the core wheel, which is why there was no extra to name.

## Backends

| `--robot` | What it is | Status |
|---|---|---|
| `toddlerbot:mock` | the daemon's answers in memory | ✅ every verb runs offline in the test suite |
| `toddlerbot:sim2d` | the cartoon world with a humanoid profile | ✅ `toddlerbot-lookout` 10 of 10 seeds |
| `toddlerbot:bridge` | the real robot, through the daemon quackd ships | 🧪 names VERIFIED at the pin, the protocol and the daemon exercised against a fake body over loopback, **never run on a robot** |

## Why quackd ships a daemon

There is nothing to talk to. No socket, no daemon, no IPC: upstream is a Python library whose
control loop opens serial ports in-process. That is the Open Duck Mini situation rather than
the Microduck one, so [`bridge/toddlerbot/`](../../bridge/toddlerbot/) holds a daemon and this
adapter is its client.

But there is a second reason, and it is the one that matters. **A verb is episodic and this
robot is not.** `RealWorld.step()` is a no-op, so nothing times out and nothing re-arms: the
last commanded pose is held forever. A humanoid frozen mid-stride while a model thinks is a
humanoid on the floor. So the daemon runs the fifty hertz loop continuously and quackd's
intents only steer what it is already doing.

## What upstream does not protect, and what quackd added

Every row here was read at the pin. Upstream's CI is disabled and its repository has one test
file, so none of it was ever machine-verified by anyone.

| What is missing upstream | What the daemon does about it |
|---|---|
| `set_motor_target` clamps nothing and never reads the joint limits, which exist. The motors are in multi-turn mode, so the firmware limits are off too. | Clamps every target against the MJCF joint ranges. |
| No rate limit anywhere. A position command is a full-torque snap. | Caps how far any joint may move per tick. |
| A failed bulk read returns an all-zeros buffer, indistinguishable from every joint at zero. Believing it commands a full-scale move to zero. | Detects it, refuses it, and holds the last verified-good pose. |
| A controller fault surfaces as a bare `KeyError`, because the C++ swallows it and inserts an empty map. | Catches it and treats it as a hardware fault, not a transient. |
| No `reset` anywhere in the sim package: no home, no safe pose. | Builds one: upstream's default pose at upstream's own 0.3 rad/s, waist first. |
| No watchdog, no timeout, no e-stop. Silence means hold forever. | Its own deadman, which slews to the safe pose and holds. It never goes limp. |
| No Python signal handler, and the C `atexit` does not run on `SIGTERM`. | Installs handlers that settle before anything may exit. |
| `close()` is bound without releasing the GIL and retries torque-off forever on a dead bus, so a stuck shutdown freezes every thread. | Arms a hard-exit timer before calling it. |
| The constructor busy-waits forever on a silent IMU, with the motors already live. | Runs construction in a thread it is willing to abandon. |
| `set_motor_kps` raises `NotImplementedError` on hardware. | Never calls it, so no shutdown path depends on softening gains first. |
| Upstream tells hardware from sim with `"real" in sim.name`. | Dispatches on type. |

## What this robot cannot do

- **`say`.** `Speaker` imports `re`, `subprocess` and `sounddevice`: it plays audio, it does
  not synthesise it. There is no text to speech at this pin, so the `sound` intent is not
  declared.
- **Walk, usually.** The gait is an ONNX checkpoint fetched from a wandb artifact. Nothing is
  checked in and the README mentions no checkpoint, entity or download at all. Without one
  `move`, `go_to` and `approach_and` **do not exist**, and `mobility` is `none`. They appear
  only when the daemon reports one staged.
- **Get up.** There is no recovery policy for this body, so `stand_up` is not declared and a
  fall ends the run. Do not confuse it with `stand`, which is a different verb for a
  different situation: `stand` slews an upright robot to the safe pose and holds it, at
  upstream's own reset rate and waist first. It is not a way back up from the floor and it
  will refuse once the robot has fallen. quackd calls it `stand` rather than `posture`
  because what it does is reach one specific pose, not choose among several.
- **Read its own IMU.** The daemon never touches `ThreadedIMU`. Orientation arrives in the
  observation as a quaternion on `rot`, which both the real body and the simulated one
  fill, so the shape that upstream's threaded IMU returns is not a fact quackd depends on
  and is not cited here. If a future verb ever needs the IMU directly, that is the point at
  which somebody has to read it at the pin.
- **Report a battery.** Bus voltage is read in C++ and only printed, so a battery abort can
  never fire here.
- **Report a position.** The observation carries motor positions and an orientation. There is
  no odometry, so `go_to` closes the loop on the camera alone.

Its datasheet, which the pilot is shown and told to judge a task against before anything moves ([manifest-spec.md](../manifest-spec.md)):

| | |
|---|---|
| Mass | 3.4 kg (official: arXiv:2502.00893) |
| Height | 0.56 m (official: arXiv:2502.00893) |
| Actuated joints | 30 (official: arXiv:2502.00893) |
| Payload | 1.484 kg (official: arXiv:2502.00893; the whole body with both arms, 40 percent of its own weight; per arm is not published) |
| Endurance | 19 min (official: the ToddlerBot project site; about) |
| Not published | reach |

And what it cannot do whatever the task says, which is the half a refusal usually turns on, in the words the pilot is shown:

- get back on its feet after a fall: there is no recovery policy, so a fall ends the run and needs a human
- carry more than about 1.5 kg with both arms together, or an unknown weight in one
- keep going for more than about twenty minutes: past that the servos heat up and balance suffers

A figure nobody published is listed as not published, and the pilot is told to answer `uncertain` and name it, rather than guess, where a task turns on it. A `.duck` file can correct any of it for the build in front of you ([duck-spec.md](../duck-spec.md)).

## The verbs this robot brings

The core verbs come from the registry. These four are the robot's own.

| Verb | What it does here |
|---|---|
| `stand` | slews an upright robot to the safe pose at upstream's own 0.3 rad/s, waist first, and holds it. Not a way up from the floor, and it refuses once the robot has fallen |
| `perform(motion)` | replays one keyframe motion open loop: hold, kneel, cuddle, push_up, crawl, and only the ones the daemon actually loaded |
| `look` | the two joint neck. Which motor is yaw is inferred from the motor names (`NECK_AXES` below) |
| `move`, `go_to`, `approach_and` | present only when the daemon reports a walk checkpoint staged, which a fresh install has not got |

## The motions, and which ones quackd offers

Nine motions ship as keyframes, and they are the only motion that works with no downloads.
quackd offers five: **hold, kneel, cuddle, push_up, crawl**.

The other four are excluded on judgement rather than capability, and the distinction matters:
the robot can do all of them. `pull_up_grasp` and `pull_up_pull` assume the robot is hanging
from a bar, so asking for one on a robot standing on a table is a fall. `walk_zmp` is a gait
reference rather than a performance, and locomotion belongs to `move` where the deadman covers
it. `cartwheel` is excluded twice over. This body has no fall recovery, and a cartwheel is not
something to discover a language model can trigger, but it also *cannot* be replayed: its file
carries `action=None` because it is qpos-interpolated and meant to run as its own RL policy.
`walk_zmp` cannot be replayed either, for a different reason: despite sitting in the same
directory with the same extension it is not a keyframe file at all, it is a gait lookup table
used at training time.

Upstream ships no loader for any of this. Every call site there reads the file with
`joblib.load` inline, so the daemon does the same. Each motion is written twice, once per
variant, and the daemon picks `_2xc` or `_2xm` from the robot name it was started with. A
keyframe file that is missing, unreadable or carries no action array is **not offered**: the
handshake reports the motions that actually loaded, and quackd's manifest lists those. A
shorter list is better than a verb that refuses on a robot.

## Running the daemon

The install, the flags and the safety notes live with the daemon, in
[`bridge/toddlerbot/README.md`](../../bridge/toddlerbot/README.md), because that is the file
an operator has open on the robot. What matters from quackd's side is the shape of the
answer:

**Three flags decide what this robot can do, and each one is checked rather than believed.**
`--camera left|right` needs upstream's `Camera` to actually open. `--walk-policy NAME` needs
a checkpoint at `ckpts/NAME/` that upstream neither publishes nor checks in, so it is a file
you supply. `--gripper` is only honoured if the robot has gripper motors. A capability the
daemon reports is a verb quackd will offer, so the daemon reports only what it loaded: a
camera that will not open means `observe` never appears.

The daemon also reads the loaded checkpoint's own `command_range` at startup and reports the
velocity envelope it was really trained on, which is what quackd's `limits` narrow to. An
envelope with nothing left on any axis is treated as no locomotion at all.

**The board it runs on can lend quackd its GPU, and quackd stays on the laptop.** This robot
carries a Jetson, which is a computer rather than a body. Beside the daemon it can hold a model
server and quackd's host daemon, and `--host` reaches both from the laptop
([jetson.md](../jetson.md)). The host daemon runs with `--camera none`, because this daemon
owns the robot's cameras. A ToddlerBot is a real body, so when the host daemon can detect, the
run uses the board's YOLO by itself: the frames this daemon's camera sends reach the laptop and
go back to the board as JPEGs to be detected, a round trip nobody has timed. `--detector color`
keeps the colour detector on the laptop instead. Nobody has measured what a model server or a
detector saturating that board does to the fifty hertz loop beside it either, and on this body
a starved loop is a fall.

## The contract job, and what a green one means

`.github/workflows/toddlerbot-contract.yml` runs nightly and on demand, and it is the only
thing here that installs upstream: a blobless sparse checkout, quackd's real daemon under
`--sim mujoco`, and quackd's real client over a real socket. Its own comments carry the
reasoning, including why `MUJOCO_GL` and `xvfb` are not needed and what is needed instead.

**A green run still means nothing about hardware.** It means the protocol, the fifty hertz
loop, the clamp and the deadman hold up against thirty simulated motors that push back,
which is strictly more than the fake body proves and strictly less than a robot.

## VERIFIED (read from upstream source on 2026-09-05, at `84e02d1`)

| Thing | Value | Used for |
|---|---|---|
| The contract | `BaseSim` | six abstract methods and no more |
| The observation | `Obs` | twelve fields, and no image field of any kind |
| Always None on hardware | `pos, lin_vel, joint_pos, joint_vel` | and `motor_acc`; there is no pose |
| Stepping does nothing | `def step(self)` | the write already happened |
| Commanding | `RealWorld.set_motor_target` | radians, absolute, and it clamps nothing |
| Dict order is not motor order | `motor_angles.values()` | quackd only ever sends an array |
| Multi-turn | `extended_position` | firmware position limits are off |
| Gains cannot be set | `NotImplementedError` | so no shutdown may depend on softening them |
| Retries are ignored | `retries` | accepted and never read |
| A dropped read is zeros | `bulk_read` | indistinguishable from a valid reading |
| A fault is not an exception | `KeyError` | the C++ swallows it and leaves an empty map |
| A units error upstream | `motor currents in Amperes` | the hardware delivers milliamps |
| Quaternion order | `scalar_first` | w first, and it puts a real floor under scipy |
| The bindings | `PYBIND11_MODULE(dynamixel_cpp, m)` | eight names and no more |
| Torque cannot be re-enabled | `enable_motors` | exists in C++, not bound to Python |
| Closing goes limp | `set_torque_enabled` | with false; on a standing robot that is a fall |
| Any exit drops the robot | `atexit(dynamixel_cleanup_handler)` | registered from the client constructor |
| Except the exits that matter | `dynamixel_cleanup_handler` | C atexit does not run on SIGTERM |
| Shutdown holds the GIL | `close` | bound without a release guard, and it retries forever |
| And it is process-global | `close_motors` | closing one controller disconnects them all |
| And it is unguarded | `self.imu.close()` | an exception there skips the motor close |
| Construction energises | `initialize` | the robot is live the moment it returns |
| Construction needs an IMU | `get_latest_state` | dereferenced with no None guard |
| Construction can hang | `while not imu_data` | no sleep, no timeout, no cap |
| Construction hides failure | `Dynamixel controller not found` | printed, and it carries on |
| No safe pose exists | `reset` | nowhere in the sim package |
| The rate to move at | `reset_vel` | 0.3 rad/s, upstream's own |
| The waist goes first | `ResetPDPolicy` | upstream's own two-phase rule |
| The loop rate | `control_dt` | 0.02 s, and nothing overrides it |
| Walking needs a download | `load_wandb_policy` | nothing is checked in |
| And all three keys | `control_inputs` | a partial command raises mid-tick |
| The real envelope | `command_range` | comes from the checkpoint, not the gin file |
| The camera | `Camera` | exists, but not in the observation |
| The speaker cannot speak | `Speaker` | it plays audio and nothing synthesises it |
| No battery in Python | `read_vin` | read in C++ and only printed |
| The version disagrees | `0.2.0` | while the tag says v2.0.0 |
| CI is off | `Skipping tests` | nothing here was machine-verified upstream |
| MuJoCo is transitive | `brax` | undeclared and unpinned |
| The builds | `Robot` | five names: four robots of thirty to thirty-two motors, and a fourteen motor leader arm |
| The only joint limits | `motor_limits` | parsed from the MJCF, never from YAML |
| The safe pose | `default_motor_angles` | a dict of radians per motor, **not** `default_motor_pos`, which is a real upstream name on a different class. quackd reads it with no fallback: zeros are a large wrong motion on this body |
| Calibration is absent | `motors.yml` | gitignored, so a fresh clone has none |
| The only offline motion | `motion` | eighteen keyframes, nine motions |
| No loader exists | `joblib.load` | every reader upstream loads the file inline |
| Motions are per variant | `robot_suffix` | `_2xc` or `_2xm`, chosen from the robot name |
| The frames | `action` | (frames, 30) float32 radians, in `motor_ordering`, at 50 Hz |
| Not replayable | `cartwheel` | `action=None`: it is an RL policy, not a keyframe |
| Not a motion at all | `walk_zmp` | a gait lookup table that shares the extension |
| The camera takes a side | `Camera.__init__` | untyped, Linux only, and synchronous |
| Frames are BGR | `Camera.get_frame` | raises rather than returning None |
| Its JPEG is wrong | `Camera.get_jpeg` | hands RGB to `imencode`, which wants BGR |
| The walk loader | `load_wandb_policy` | returns a directory, not the dict it annotates |
| The walk step | `WalkPolicy.step` | takes the observation and the sim, returns a pair |
| The envelope | `command_range` | rows 5, 6 and 7 are the walk velocities |
| All three or none | `control_inputs` | a partial dict raises mid-tick |
| Never clipped upstream | `walk_x` | out-of-envelope goes straight to the network, so quackd clamps to the envelope before sending |
| The simulated body | `MuJoCoSim` | takes the Robot, runs at the same fifty hertz |
| Headless by default | `vis_type` | only render or view build anything that needs GL |
| Do not use it | `controller_type` | the position controller's step takes the wrong arity |
| Paths are relative | `scene.xml` | so the daemon changes directory to the checkout root |

## UNVERIFIED, and what quackd does about it

| Name | The assumption | What quackd does |
|---|---|---|
| `DAEMON_PROTOCOL` | the wire is quackd's own at both ends | there is no upstream to verify it against, so citing one would be a false citation. What is cited instead is every assumption the daemon makes |
| `SAFE_POSE` | upstream's default pose is safe to slew to | true from standing; from a crawling or prone start it is untested and must be tried on a stand |
| `FALL_DETECTION` | a tilt past 50 degrees is a fall | there is no fall detection upstream at all; the threshold is a guess until somebody tips a real robot |
| `GRIPPER_AXES` | which end of a gripper's travel is closed | upstream states it nowhere, so the daemon takes the MJCF range's low end as closed and finds the motors by name. The capability is reported only when a gripper motor is actually found, never because the flag was passed |
| `NECK_AXES` | which neck motor is yaw | inferred from the motor names rather than stated anywhere |
| `ZERO_LATCHING` | a calibrated zero survives `initialize` | reading did not settle whether the configured zero is re-latched at every startup; the daemon refuses to actuate without the file either way |
| `WALK_POLICY_IS_STATEFUL` | the gait policy can be driven only while `move` runs | it keeps a history and a buffer, expects a fixed fifty hertz, and ignores its commands for the first seven seconds. quackd steps it only while walking, so that window opens on the first command rather than at startup, and whether a gait driven that way behaves like one driven continuously is untested |
| `THREAD_SAFETY` | the C++ is not safe across threads | two of eight bindings release the GIL and six do not, so every call is serialised onto the control thread and the camera stays on another |

## How to help

If you have built a ToddlerBot, **put it on its safety stand first**, and work through
[toddlerbot-hardware-checklist.md](../toddlerbot-hardware-checklist.md) in order: it is
written so that each step can only fail in a way you can recover from, and it keeps the
feet off the ground until step 13. Then run quackd's daemon,
point `toddlerbot-lookout` at it, and say what happened: that task's allowlist moves no leg, no
arm and no waist. What most needs a real robot: whether the safe-pose slew is actually safe
from a crawl, what tilt angle really means fallen, whether the neck axes are what the motor
names imply, and whether a calibrated zero survives a restart.
