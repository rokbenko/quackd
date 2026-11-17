# ToddlerBot

A small open source humanoid, about 56 cm and 3 kg, with two arms, two legs, a two joint neck
and thirty Dynamixel servos. It is Roboglot's first full humanoid, and the third body whose
robot side Roboglot ships, because upstream has no network API of any kind.

Upstream: [hshi74/toddlerbot](https://github.com/hshi74/toddlerbot), pinned at
[`84e02d1`](https://github.com/hshi74/toddlerbot/tree/84e02d14261292eec5d06f896e3145b35c54856c),
which is the commit the annotated tag `v2.0.0` points at (read 2026-09-05). Note the tag
object's own sha, `6fde5df5`, is **not** a commit and pinning it gets a 422. Code is MIT; **the
design, including everything under `descriptions/`, is CC BY-NC-SA 4.0 and therefore
non-commercial**, so Roboglot vendors none of it.

**Nothing here has ever run on a robot.**

```bash
uv pip install 'roboglot[toddlerbot]'

# offline
roboglot run toddlerbot-lookout --robot toddlerbot:mock --llm fake

# a real robot, once Roboglot's daemon is running on it
roboglot run toddlerbot-lookout --robot toddlerbot:bridge \
  --address tcp://toddlerbot.local:9873
```

That extra dates from 0.10.0, and it installs exactly one package: `roboglot-toddlerbot`,
Roboglot's own Apache-2.0 adapter, whose only dependency is Roboglot itself. The client is stdlib,
and nothing of upstream's is in it, the non-commercial `descriptions/` included. Before 0.10.0
the adapter was part of the core wheel, which is why there was no extra to name.

## Backends

| `--robot` | What it is | Status |
|---|---|---|
| `toddlerbot:mock` | the daemon's answers in memory | ✅ every verb runs offline in the test suite |
| `toddlerbot:bridge` | the real robot, through the daemon Roboglot ships | 🧪 names VERIFIED at the pin, the protocol and the daemon exercised against a fake body over loopback, **never run on a robot** |

## Why Roboglot ships a daemon

There is nothing to talk to. No socket, no daemon, no IPC: upstream is a Python library whose
control loop opens serial ports in-process. That is the Open Duck Mini situation rather than
the Microduck one, so [`bridge/toddlerbot/`](../../../bridge/toddlerbot/) holds a daemon and this
adapter is its client.

But there is a second reason, and it is the one that matters. **A verb is episodic and this
robot is not.** `RealWorld.step()` is a no-op, so nothing times out and nothing re-arms: the
last commanded pose is held forever. A humanoid frozen mid-stride while an LLM thinks is a
humanoid on the floor. So the daemon runs the fifty hertz loop continuously and Roboglot's
intents only steer what it is already doing.

## What upstream does not protect, and what Roboglot added

Every row here was read at the pin. Upstream's CI is disabled and its repository has one test
file, so none of it was ever machine-verified by anyone.

| What is missing upstream | What the daemon does about it |
|---|---|
| `set_motor_target` clamps nothing and never reads the joint limits, which exist. The motors are in multi-turn mode, so the firmware limits are off too. | Clamps every target against the MJCF joint ranges. |
| No rate limit anywhere. A position command is a full-torque snap. | Caps how far any joint may move per tick. |
| A failed bulk read returns an all-zeros buffer, indistinguishable from every joint at zero. Believing it commands a full-scale move to zero. | Detects it, refuses it, and holds the last verified-good pose. |
| A controller fault surfaces as a bare `KeyError`, because the C++ swallows it and inserts an empty map. | Catches it and treats it as a hardware fault, not a transient. |
| No `reset` anywhere in the sim package: no home, no safe pose. | Builds one: upstream's default pose at upstream's own 0.3 rad/s, waist first. |
| No watchdog, no timeout, no e-stop. Silence means hold forever. | Its own deadman, which slews to the safe pose and holds. It never goes limp. It watches each part (the body, the neck, each gripper) for the connection whose command that part is executing, and brings only a silent owner's parts to rest. A part nobody owns, holding after a stop or never commanded, it never moves, and on a fallen robot rest is holding where it lies. |
| No Python signal handler, and the C `atexit` does not run on `SIGTERM`. | Installs handlers that settle before anything may exit, and refuses every command that moves the robot while it settles. |
| `close()` is bound without releasing the GIL and retries torque-off forever on a dead bus, so a stuck shutdown freezes every thread. | Arms a hard exit that needs no GIL before the settle and again before `close()`, so a hung bus cannot keep the process alive. |
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
  will refuse once the robot has fallen. Roboglot calls it `stand` rather than `posture`
  because what it does is reach one specific pose, not choose among several.
- **Read its own IMU.** The daemon never touches `ThreadedIMU`. Orientation arrives in the
  observation as a quaternion on `rot`, which the real body fills, so the shape that upstream's
  threaded IMU returns is not a fact Roboglot depends on and is not cited here. If a future verb
  ever needs the IMU directly, that is the point at which somebody has to read it at the pin.
- **Report a battery.** Bus voltage is read in C++ and only printed, so a battery abort can
  never fire here.
- **Report a position.** The observation carries motor positions and an orientation. There is
  no odometry, so `go_to` closes the loop on the camera alone.

Its datasheet, which the pilot is shown and told to judge a task against before anything moves ([reference/manifest-spec.md](../../reference/manifest-spec.md)):

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

A figure nobody published is listed as not published, and the pilot is told to answer `uncertain` and name it, rather than guess, where a task turns on it. A `.glot` file can correct any of it for the build in front of you ([reference/glot-spec.md](../../reference/glot-spec.md)).

## The verbs this robot brings

The core verbs come from the registry. These four are the robot's own.

| Verb | What it does here |
|---|---|
| `stand` | slews an upright robot to the safe pose at upstream's own 0.3 rad/s, waist first, and holds it. Not a way up from the floor, and it refuses once the robot has fallen |
| `perform(motion)` | replays one keyframe motion open loop: hold, kneel, cuddle, push_up, crawl, and only the ones the daemon actually loaded |
| `look` | the two joint neck. Which motor is yaw is inferred from the motor names (`NECK_AXES` below) |
| `move`, `go_to`, `approach_and` | present only when the daemon reports a walk checkpoint staged, which a fresh install has not got |

A `stop` from beside any of these ends it, and the robot holds where it is, as its stop always has,
the head and the grippers included. A stop the daemon did not answer, or refused, is reported as
not delivered rather than as a stop, with words that point at the deadman and away from the
power switch, which on this robot is a fall. Roboglot then stops keeping the robot alive: the
daemon's deadman trips within half a second and brings what this session was driving to rest,
the heartbeat fails saying why, and the next command the daemon accepts, or the stop's own late
answer, starts the keepalive again. The daemon accepts a stop in every state; while it shuts down
or after its loop has given up, the robot is already being brought to rest, and the stop's note
says so.

## The motions, and which ones Roboglot offers

Nine motions ship as keyframes, and they are the only motion that works with no downloads.
Roboglot offers five: **hold, kneel, cuddle, push_up, crawl**.

The other four are excluded on judgement rather than capability, and the distinction matters:
the robot can do all of them. `pull_up_grasp` and `pull_up_pull` assume the robot is hanging
from a bar, so asking for one on a robot standing on a table is a fall. `walk_zmp` is a gait
reference rather than a performance, and locomotion belongs to `move` where the deadman covers
it. `cartwheel` is excluded twice over. This body has no fall recovery, and a cartwheel is not
something to discover an LLM can trigger, but it also *cannot* be replayed: its file
carries `action=None` because it is qpos-interpolated and meant to run as its own RL policy.
`walk_zmp` cannot be replayed either, for a different reason: despite sitting in the same
directory with the same extension it is not a keyframe file at all, it is a gait lookup table
used at training time.

Upstream ships no loader for any of this. Every call site there reads the file with
`joblib.load` inline, so the daemon does the same. Each motion is written twice, once per
variant, and the daemon picks `_2xc` or `_2xm` from the robot name it was started with. A
keyframe file that is missing, unreadable or carries no action array is **not offered**: the
handshake reports the motions that actually loaded, and Roboglot's manifest lists those. A
shorter list is better than a verb that refuses on a robot.

## Running the daemon

The install, the flags and the safety notes live with the daemon, in
[`bridge/toddlerbot/README.md`](../../../bridge/toddlerbot/README.md), because that is the file
an operator has open on the robot. What matters from Roboglot's side is the shape of the
answer:

**Three flags decide what this robot can do, and each one is checked rather than believed.**
`--camera left|right` needs upstream's `Camera` to actually open. `--walk-policy NAME` needs
a checkpoint at `ckpts/NAME/` that upstream neither publishes nor checks in, so it is a file
you supply. `--gripper` is only honoured if the robot has gripper motors. A capability the
daemon reports is a verb Roboglot will offer, so the daemon reports only what it loaded: a
camera that will not open means `observe` never appears.

The daemon also reads the loaded checkpoint's own `command_range` at startup and reports the
velocity envelope it was really trained on, which is what Roboglot's `limits` narrow to, and it
holds every walk command to that envelope whoever sends it. An envelope with nothing left on any
axis is treated as no locomotion at all. Roboglot takes from the handshake only what it can
repeat safely: a motion it does not offer is left out of `perform`, which offers only the motions
the daemon loaded, and a robot name, motor count or envelope of the wrong kind refuses the
connect.

**Who may drive it.** The daemon binds `127.0.0.1` by default and is reached through an ssh
tunnel. On any other `--host` it refuses to start without a token, unless it is given
`--allow-no-token`; `--token-file` is the way to give it one. The token crosses the network in
the clear, because there is no TLS. When a robot reached over an address that is not loopback
checks no token, Roboglot warns, and `roboglot doctor` shows the warning. The daemon's README
has the details and the exact messages.

**Each part has its own owner.** The body (a walk, a stand or a motion), the neck and each
gripper are owned by the connection whose command each is executing, and by nobody while
holding: after a stop, after a stand or a motion has played to its end, or when never
commanded. A connection's keepalive keeps alive only its own parts, and when it goes quiet or
away the deadman brings those parts, and only those, to rest. So a second client's look or grip
never takes over a first client's walk, and the walk ends with its client. A command that takes
a part another connection was driving tells that connection. A motion moves the head and the
hands too, so it takes them with the body, except what its own client holds by a look or a grip,
which stays held through it (a hand closed on something stays closed, and the answer lists it as
kept); a look or a grip its client makes during the motion stays that client's when the motion
ends, as one made outside a motion does. It is refused, with the reason, while another
connection drives one of them or one is still coming to rest, which a stop ends at once. A stop is honoured from anyone,
stops every part and leaves every part owned by nobody: it arms nothing, so a `roboglot doctor`
check, which sends a stop as it disconnects, moves nothing on a robot nobody is driving. Against
a run in flight it stops the run's motion and the robot holds; the run is told at its next
keepalive or command, and its heartbeat fails with the reason. Run doctor checks between runs.

**Motions that go to the floor.** `crawl` and `push_up` lie the robot down on purpose, face down
and past the fall threshold, so they play to their end and the robot lying face down after one is
not read as fallen. Its posture reads `unknown`, never `standing`, a walk or an upright motion
waits, and `stand` brings it back up. A stand from the floor is done only once the trunk reads
upright. One that cannot lift the robot by its deadline stops pushing: it holds the robot where
it lies, as after a fall, health says it did not get up, and another `stand` may try again. A lean
any other way, on its side or its back, during the motion, after it or standing back up, is a
fall. Which motions those are is a table in the daemon (`FLOOR_MOTIONS`), by what each is named
for, because the keyframes carry no orientation of the trunk to read it from. Which way is face
down in the orientation it reads (`FACE_DOWN`, below) is an assumption no robot has checked yet,
and step 13 of the hardware checklist is the reading that checks it.

**After the loop gives up.** Ten failing ticks in a row and the daemon takes no more orders: it
settles the robot to its safe pose while the bus answers, and only a restart of the daemon lets
it take orders again. Until then every command that moves the robot is refused with the reason
(`the control loop gave up after 10 failing ticks in a row: ... Restart the daemon`), a stop is
accepted with that reason as its note, since the robot is already being brought to rest, and the
heartbeat fails on the daemon's health, which says the same or names the fault.

**The board it runs on can lend Roboglot its GPU, and Roboglot stays on the laptop.** This robot
carries a Jetson, which is a computer rather than a body. Beside the daemon it can hold an LLM
server and Roboglot's host daemon, and `--host` reaches both from the laptop
([guides/jetson.md](../../guides/jetson.md)). The host daemon runs with `--camera none`, because
this daemon owns the robot's cameras. A ToddlerBot is a real body, so when the host daemon can
detect, the run uses the board's YOLO by itself: the frames this daemon's camera sends reach the
laptop and go back to the board as JPEGs to be detected, a round trip nobody has timed.
`--detector color` keeps the colour detector on the laptop instead. Nobody has measured what an LLM
server or a detector saturating that board does to the fifty hertz loop beside it either, and on
this body a starved loop is a fall.

## VERIFIED (read from upstream source on 2026-09-05, at `84e02d1`)

| Thing | Value | Used for |
|---|---|---|
| The contract | `BaseSim` | six abstract methods and no more |
| The observation | `Obs` | twelve fields, and no image field of any kind |
| Always None on hardware | `pos, lin_vel, joint_pos, joint_vel` | and `motor_acc`; there is no pose |
| Stepping does nothing | `def step(self)` | the write already happened |
| Commanding | `RealWorld.set_motor_target` | radians, absolute, and it clamps nothing |
| Dict order is not motor order | `motor_angles.values()` | Roboglot only ever sends an array |
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
| The safe pose | `default_motor_angles` | a dict of radians per motor, **not** `default_motor_pos`, which is a real upstream name on a different class. Roboglot reads it with no fallback: zeros are a large wrong motion on this body |
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
| Never clipped upstream | `walk_x` | out-of-envelope goes straight to the network, so Roboglot clamps to the envelope before sending, and the daemon holds any client to it again |

## UNVERIFIED, and what Roboglot does about it

| Name | The assumption | What Roboglot does |
|---|---|---|
| `DAEMON_PROTOCOL` | the wire is Roboglot's own at both ends | there is no upstream to verify it against, so citing one would be a false citation. What is cited instead is every assumption the daemon makes |
| `SAFE_POSE` | upstream's default pose is safe to slew to | true from standing; from a crawling or prone start it is untested and must be tried on a stand |
| `FALL_DETECTION` | a tilt past 50 degrees is a fall | there is no fall detection upstream at all; the threshold is a guess until somebody tips a real robot |
| `FACE_DOWN` | face down, the world's up leans along -x in the body's frame | it follows from upstream's orientation being the body's in the world and the IMU's axes being the torso's, neither of which anything read shows. One reading on the stand settles it (step 13 of the checklist): tipped nose down, the x of `up_in_body` in `report_state` reads below zero. On a robot where the sign is the other way round, `crawl` and `push_up` are cut halfway and read as a fall, a fall onto its back during them goes unseen, and nothing outside the two floor motions is affected |
| `GRIPPER_AXES` | which end of a gripper's travel is closed | upstream states it nowhere, so the daemon takes the MJCF range's low end as closed and finds the motors by name. The capability is reported only when a gripper motor is actually found, never because the flag was passed |
| `NECK_AXES` | which neck motor is yaw | inferred from the motor names rather than stated anywhere |
| `ZERO_LATCHING` | a calibrated zero survives `initialize` | reading did not settle whether the configured zero is re-latched at every startup; the daemon refuses to actuate without the file either way |
| `WALK_POLICY_IS_STATEFUL` | the gait policy can be driven only while `move` runs | it keeps a history and a buffer, expects a fixed fifty hertz, and ignores its commands for the first seven seconds. Roboglot steps it only while walking, so that window opens on the first command rather than at startup, and whether a gait driven that way behaves like one driven continuously is untested |
| `THREAD_SAFETY` | the C++ is not safe across threads | two of eight bindings release the GIL and six do not, so every call is serialised onto the control thread and the camera stays on another |

## How to help

If you have built a ToddlerBot, **put it on its safety stand first**, and work through
[adapters/toddlerbot/hardware-checklist.md](hardware-checklist.md) in order: it is
written so that each step can only fail in a way you can recover from, and it keeps the
feet off the ground until step 14. Then run Roboglot's daemon,
point `toddlerbot-lookout` at it, and say what happened: that task's allowlist moves no leg, no
arm and no waist. What most needs a real robot: whether the safe-pose slew is actually safe
from a crawl, what tilt angle really means fallen, which way face down reads, whether the neck
axes are what the motor names imply, and whether a calibrated zero survives a restart.
