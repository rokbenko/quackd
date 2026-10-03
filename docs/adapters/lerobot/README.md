# LeRobot (an SO-101 class arm)

A six-joint desktop arm with a parallel gripper, driven through
[LeRobot](https://github.com/huggingface/lerobot). No legs, no head, no voice, so its
manifest lists none of that: `move`, `go_to`, `search_scan`, `say` and `gaze` do not exist
on this robot. What it has is joints, a gripper, `place`, and, when a run names a policy
server, or on the mock, which scripts its own, `pick` and `manipulate`, each one skill intent
that the arm's own learned policy executes ([policies.md](../policies.md)). The thesis holds:
the LLM picks the verb, LeRobot moves the arm, quackd enforces the contract. (With the optional `--decision-llm`, some of the verbs that are a choice rather than a number can be picked by a decision LLM instead; every angle is still the model's, and it is off unless you name one: [decision-llms.md](../decision-llms.md).)

Upstream pinned at
[`fbb811f`](https://github.com/huggingface/lerobot/tree/fbb811fca92504439792b97d216f0d00c2268382)
(`main`, 2026-09-01), first read 2026-09-02 and read again on 2026-09-13. Every name quackd
spells lives in
[`adapters/lerobot/src/quackd_lerobot/upstream_api.py`](../../adapters/lerobot/src/quackd_lerobot/upstream_api.py),
and why the adapter is shaped the way it is is
[ADR-0036](../adr/0036-what-the-arm-does-not-say.md).

> [!NOTE]
> **The `real` backend has run on an arm.** On 2026-09-15 an SO-101 follower, calibrated as
> `arm-01` and reached with no registered name, ran `lerobot-lookout` and then free-form
> `--goal` runs on Windows 11 with Python 3.12.12, lerobot 0.6.1 and quackd 0.9.0, piloted by
> OpenAI's `gpt-6-astra`. It also went limp and fell at the end of every one of those runs,
> which is what [the rest pose](#the-rest-pose) exists to fix. On 2026-09-23 the same arm ran
> again with a rest pose recorded, and the pose lay past the travel its calibration recorded,
> where the servo will not be driven: [The rest pose](#the-rest-pose) says what that did and
> what quackd does about it now. What ran, what went wrong and what that first afternoon did not
> measure is in [Status](#status); the account from the other end, an empty laptop to a waving
> arm, is [lerobot-first-run.md](../lerobot-first-run.md).

```bash
# offline, the default
uv run quackd run lerobot-lookout --robot lerobot:mock --llm fake

uvx --from "quackd[lerobot]" quackd list-verbs --robot lerobot:mock
uvx --from "quackd[lerobot]" quackd validate ducks/find-and-kick.duck --robot lerobot:mock     # exit 1: requires ... does not provide it
uvx --from "quackd[lerobot,microduck]" quackd serve-mcp --robots arm=lerobot:mock,duck=microduck:sim2d   # an arm and a duck behind one MCP server

# the arm's simulator: the real backend's own code over a physics model of the arm, no arm needed
uvx --from "quackd[lerobot-sim]" quackd run lerobot-lookout --robot lerobot:mujoco --llm fake

# a real arm, after LeRobot's own calibration (see the checklist)
uv pip install "quackd[lerobot]" && quackd doctor --robot lerobot:real --address /dev/ttyACM0   # Python 3.12+
```

## If you already own one

You have LeRobot, and LeRobot already does what this arm is known for: teleoperation from a
leader, recording episodes, training a policy, evaluating one. quackd does none of that and
is not trying to. It does the one thing LeRobot leaves to you, which is deciding what the arm
should do next, and it does it by putting a language model in that seat under a contract the
model cannot exceed.

| What you want | What does it |
|---|---|
| drive the follower from a leader arm, record a dataset, train a policy | LeRobot's own tools. quackd never calls them and never writes to your datasets |
| have a model choose the next verb, inside limits you wrote down, with every refusal recorded | quackd |
| hand the arm to a policy you trained, one short subtask at a time, under the model and inside those limits | quackd's policy server, `quackd policy serve`, which loads a LeRobot checkpoint in a process of its own ([policies.md](../policies.md)) |
| run one verb by hand, right now | quackd over MCP (`robot_run_verb`), or LeRobot's own Python API |

The contract is a `.duck` file: the verbs the model may use, the budget in steps and minutes,
and what counts as success ([duck-spec.md](../duck-spec.md)). The model never emits a motor
command. It picks a verb, and quackd checks the allowlist, the preconditions, this arm's
calibrated range and the step cap before anything reaches the bus.

Three things are true of this body, and on a desk rather than in a simulator they are what can
hurt somebody. They shape everything below:

- **The five body joints have no torque cap.** LeRobot writes a torque and current cap on the
  gripper and on nothing else, so a stalled elbow has nothing to save it or your finger. quackd
  reads each servo's temperature off the bus and refuses to move a joint at or above 60 °C.
- **A goal in degrees is not clamped by LeRobot.** quackd computes each joint's travel from
  your calibration file and refuses a goal outside it, rather than passing the number down.
- **There is no e-stop and no deadman.** Nothing in LeRobot stops the arm when the controlling
  process goes quiet, and a position-controlled servo holds the last goal it was given. Cutting
  the servo supply is the only thing that stops this arm in every case.

## Start here

If you have never run quackd or LeRobot before, start at
[lerobot-first-run.md](../lerobot-first-run.md) instead: it is this arm from an empty laptop,
including which model to bring and what it can actually see. This page assumes you already
drive the arm.

1. **Run it with no arm attached.** `lerobot:mock` is the same verbs, the same executor and
   the same refusals, in memory, so you can see what a run looks like before you risk
   anything:

   ```bash
   uvx --from "quackd[lerobot]" quackd run lerobot-lookout --robot lerobot:mock --llm fake
   ```

   The scripted pilot needs no API key. It answers `assess_task`, calls `report_state`, and
   declares:

   ```
   +  declare success: elbow_flex 90, gripper 100, shoulder_lift -90, shoulder_pan 0, wrist_flex 0, wrist_roll 0; torque on; nothing hot
   ```

   The mock is quackd's own stand-in and runs none of the real backend's code.
   [The simulator](#the-simulator-lerobotmujoco), `lerobot:mujoco`, runs all of it over a
   physics model of the arm, which is where a task file is rehearsed before it meets one.

2. **Bring the real arm up in the order that can only fail safely.**
   [lerobot-hardware-checklist.md](../lerobot-hardware-checklist.md) is eighteen steps, and
   nothing moves until step 10 as long as the arm stays where step 6 records its rest pose:
   move it by hand after that, and the next run or `doctor` drives it back there. Do not skip
   the calibration step: quackd refuses an arm that has not been calibrated, because the
   calibration file is where every joint's travel comes from.
3. **Then drive it**, which is [three commands](#driving-it) depending on who is choosing the
   verbs: a task file, a one-line goal, or you from an MCP client.

## Backends

| `--robot` | Status | What it is |
|---|---|---|
| `lerobot:mock` | ✅ | an arm in memory: goals land instantly, the gripper stops on the object, a scripted policy answers `pick` and `manipulate`, and it refuses an out-of-range goal in the same words the real one does |
| `lerobot:real` | ✅ | an SO-101 follower through LeRobot (extra `quackd[lerobot]`, Python 3.12 or newer, torch), and as many USB webcams as `--camera-url` names; every name VERIFIED at the pin, exercised against a fake arm and a fake camera, and run on one real arm on two afternoons, 2026-09-15 on quackd 0.9.0 and 2026-09-23 on quackd 0.12.0, with lerobot 0.6.1 and no policy. What changed after 2026-09-23 has not run on an arm yet |
| `lerobot:mujoco` | ✅ | [the arm's simulator](#the-simulator-lerobotmujoco): `lerobot:real`'s own code over a physics model of the SO-101 in MuJoCo, the maker's model fetched at a pinned commit, with the cameras rendered from the scene (extra `quackd[lerobot-sim]`, Python 3.11 or newer, no LeRobot and no torch). A seeded grasp sweep and `quackd preflight` sweeps pass on the maker's model, and nothing has compared it against an arm, so it never raises `lerobot:real`'s status ([adapter-status.md](../adapter-status.md)) |

`--address` is the arm's serial port (`/dev/ttyACM0`, `COM5`), and quackd checks that it
looks like one before LeRobot opens anything. The `real` backend calls
`connect(calibrate=False)` and refuses an uncalibrated arm, in these words:

```
lerobot real: the arm is not calibrated; run LeRobot's calibration first
(it is interactive, quackd never triggers it)
```

Calibration is upstream's own interactive step, under the id quackd will use, and it writes
the file every joint's range is read from: step 5 of
[lerobot-hardware-checklist.md](../lerobot-hardware-checklist.md).

## Installing it

A bare `uv pip install quackd` brings no robot at all, this arm included. The adapter is its
own distribution, `quackd-lerobot`, and the extra is what pulls it in:

```bash
uv pip install "quackd[lerobot]"
uv run quackd doctor
```

That is two packages: quackd's own adapter, which is where all three backends live, and
`lerobot[feetech]`, the SDK `real` drives the arm with. The adapter announces
itself through the `quackd.adapters` entry point group, so there is nothing to register by
hand. `uv pip install quackd-lerobot` is that adapter without the SDK, which is enough for
`lerobot:mock` and for reading the manifest, and enough for nothing else.

Two rows in `doctor` decide whether a serial port can be opened at all:

```
· lerobot                    not installed (quackd[lerobot])
· lerobot (feetech bus)      not installed (quackd[lerobot])
```

The second one is the trap. The Feetech SDK lives in LeRobot's own `[feetech]` extra rather
than in its base dependencies, so a `pip install lerobot` gives you a package that imports
perfectly and then cannot talk to a motor. `quackd[lerobot]` asks for `lerobot[feetech]` for
that reason. Both rows have to be green before `lerobot:real` can do anything.

The SDK carries a `python_version >= '3.12'` marker, because that is LeRobot's floor while
quackd's own is 3.11. On 3.11 the extra installs the adapter and the SDK resolves to nothing,
so `lerobot:mock` works and `doctor` keeps saying `not installed` however many times you
install it: check `python --version` first.

Without the extra, every real-arm command ends the same way, and this is what it looks like:

```
+- x FAILURE ------------------------------------------------------------------+
| lerobot:real at COM5: adapter 'lerobot' needs an extra: uv pip install       |
| 'quackd[lerobot]'                                                            |
+------------------------------------------------------------------------------+
```

The simulator is a third extra, and it needs neither LeRobot nor torch:

```bash
uv pip install "quackd[lerobot-sim]"   # the adapter and MuJoCo, on Python 3.11 or newer
```

It is `quackd-lerobot[sim]`, the same adapter with MuJoCo beside it, so it installs on 3.11 as
well, where `quackd[lerobot]` leaves you only the mock. `doctor` gives it a row of its own,
`lerobot-sim (mujoco)`, and a line in its transports table saying whether the SO-101's model is
in the cache yet:

```
| lerobot:mujoco | mujoco 3.13.0 | SO-ARM100 at 5f6d2b8: not in the cache yet, and the first connect        |
|                |               | fetches it                                                               |
```

The policy server is a fourth, installed where a policy runs, on the laptop beside the arm or
on a rented GPU, and never needed by the process that drives the arm:

```bash
uv pip install "quackd[lerobot-vla]"   # LeRobot, torch and transformers, on Python 3.12
```

It is `quackd-lerobot[vla]`, which is `lerobot[smolvla]` with no `[feetech]`, since the server
never opens a serial port. `doctor` gives it the row `lerobot-vla (transformers)`, read from the
installer's metadata so that asking costs no import of transformers.

## The name you give the arm is its calibration id

This catches people once, and the symptom is an arm that refuses to connect after a
calibration you watched succeed.

LeRobot stores a calibration under an **id** you choose, at
`<calibration dir>/robots/so_follower/<id>.json`. quackd asks LeRobot for the arm under the
id the manifest carries, and that id comes from the name you used:

| How you name it | The manifest id, and the calibration file LeRobot must have |
|---|---|
| `--robot lerobot:real` | `arm-01`, the default |
| `--robots arm=lerobot:real` | `arm` |
| `quackd robot add lab-arm lerobot:real --address COM5`, then `--robot lab-arm` | `lab-arm` |

So calibrate under the name you intend to use:

```bash
lerobot-calibrate --robot.type=so101_follower --robot.port=/dev/ttyACM0 --robot.id=lab-arm
```

Two arms sharing an id share one file, and nothing in it says which arm it came from. `doctor`
prints the path it actually loaded, which is the fastest way to see that you calibrated `arm`
and are connecting as `arm-01`.

Registering the arm is worth it beyond the id: `quackd robot add` stores the address, every
camera url and the token, so `--robot lab-arm` carries all of them and you stop retyping a COM
port ([registry.md](../registry.md)). It is also the only place a
[rest pose](#the-rest-pose) can live, which is what stops the arm falling when a run ends.

## The manifest

```json
{
  "manifest": 1, "id": "arm-01", "vendor": "huggingface", "model": "lerobot-so101",
  "embodiment": "arm", "mobility": "none",
  "intents": ["joint", "gripper", "skill"], "sensors": ["joint_state", "camera"],
  "verbs": ["observe", "report_state", "stop", "move_joints", "gripper", "place", "pick", "manipulate"],
  "preconditions": {"move_joints": ["torque_on", "not_hot"], "place": ["holding"], "pick": ["torque_on", "not_hot"], "manipulate": ["torque_on", "not_hot"]},
  "safety_authority": {"native": "torque_limit", "deadman": false, "heartbeat_hz": 2.0},
  "frame": {"reference": "base", "note": "joint space in degrees (gripper 0..100); no camera-to-base calibration"},
  "limits": {"joint_deg": 180.0, "gripper": 100.0},
  "extras": {"robot_type": "so101_follower", "joints": ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"], "policy": true, "torque_limit_scope": "gripper_only"}
}
```

That is the mock's static manifest. The static manifest of `lerobot:real` claims no camera, and
claims `pick` and `manipulate` only when `--policy-url` names a policy server
([below](#a-run-with-a-policy-server)), so a task that allows them is judged before anything
connects. `connect()` adds `observe` when `--camera-url` named a camera and it opened, and `pick`
and `manipulate` when a policy object was injected in code. It also adds what cannot be known
until the arm has answered: `extras.joint_range_deg`, every joint's travel in degrees read out of
the calibration file, to a tenth of a degree rounded inward so that every angle it names is one
the arm accepts (`wrist_roll` included, where that travel is the whole turn upstream
records rather than anything swept, see Safety), `extras.calibration_file`, the path that came
from, `extras.rest_pose_clipped` when the recorded rest pose lies past that travel
([A pose past the travel](#a-pose-past-the-travel)), `limits.step_deg`,
how far one action may move a joint (`QUACKD_LEROBOT_MAX_STEP_DEG` sets it), and
`extras.camera` with `limits.camera_fov_deg` when there is a camera. With more than one camera
it also carries `extras.cameras`, their names in the order the urls were given, primary first.
That key is written only when there are two or more: a single camera's name is quackd's own
default rather than anything you chose, and a pilot told its one view is called `front` starts
naming it in sentences nobody needs.

| Verb | Kind | What it does here |
|---|---|---|
| `observe` (alias `get_frame`) | core | one frame plus detections, only when a camera is configured. With several cameras it is the primary's frame, because a detection is a bearing and a bearing belongs to one lens |
| `report_state` | core | joint positions in degrees, whether torque is on, each servo's temperature, whether something is held, and a clause for any joint that reads more than 2 degrees past its travel |
| `stop` | core | hold: the present position becomes the goal of every body joint inside its travel. Never limp (see Safety) |
| `move_joints(positions, duration_s)` | extension | goal angles for one or more of the six joints, walked there across `duration_s` seconds one goal a tick at 10 Hz, then re-sent until the measurement arrives; the step cap is the ceiling on speed, and a joint that stops short is a failure |
| `gripper(open)` | extension | open or close the gripper, and report where it stopped |
| `place` | extension | open the gripper where the arm is; needs `holding` |
| `pick(target, max_s)` | extension, **confirm** | one skill intent; the arm's learned policy runs its own observe/act loop at its own rate until something is held or the time is up, told a target held to the one line of plain text `manipulate`'s instruction is |
| `manipulate(instruction)` | extension, **confirm** | one skill intent. The arm's learned policy is told one short subtask and runs for one segment, which ends on its time, its chunks or the arm no longer moving. It never says the task is done: the pilot looks at the arm to judge |

Joints are named, not numbered, and a `move_joints` call may name any subset of them:

```json
{"positions": {"wrist_roll": 10, "elbow_flex": 45}, "duration_s": 2.0}
```

The five body joints are degrees, centred on zero, and the gripper is `0..100` whatever the
others are.

`duration_s` is how long the motion should take, from 0.2 to 12 seconds, and 5 when the pilot
names none. The arm is read once, and every tick sends a goal as far along the straight line
from where each joint is to where it was asked to be as the time is through: halfway at half the
time, every joint starting together and arriving together. Once the time is up the goal
itself goes out, tick after tick, until every joint is within 5 degrees of it (5 units on the
gripper). So "slowly" is something a pilot can ask for, and a longer time is a slower move.
Each tick's intent still carries the `duration_s` the pilot asked for, so the record shows the
ramp and the time it was meant to take.

The step cap stays the ceiling. One action moves a joint at most 5 degrees and ten go out a
second, so nothing moves faster than 50 degrees a second, whatever `duration_s` says. A time too
short for the distance is neither refused nor obeyed: the goal runs ahead of the arm, LeRobot
clips every send to one step from where the joint is, and the joint travels at the cap until it
is there, later than asked. Those are the defaults: `QUACKD_LEROBOT_MAX_STEP_DEG` sets another
step, above 0 and up to 180 degrees.

A small move is walked like a long one: a nudge of three degrees asked to take four seconds
takes the four seconds. Until 2026-09-24 a move whose every joint already read within 5 degrees
of its goal went out whole and was judged after one tick, a tenth of a second whatever
`duration_s` said, and with a lowered step cap the one send moved the joint a step and the verb
still said it had moved.

Four exceptions, each on purpose:

- **A move with nothing to walk goes out at once.** When every joint is already within a tenth
  of a degree of its goal (a tenth of a unit on the gripper), the resolution the ramp's targets
  are sent in, there is no target between where it is and where it was asked to be. The goal is
  sent once and judged after one tick.
- **A goal outside the travel is refused before anything moves.** The verb checks every goal
  against the travel the manifest published, which is the travel the pilot was shown, and
  refuses one outside it in the words the backend refuses with, before it reads the arm or sends
  anything. Ramped, the arm would travel to the edge of its travel and be refused there. The
  published travel is rounded inward to a tenth, so a goal less than a tenth of a degree past
  its edge is refused too, although the backend's exact travel would take it: it used to be
  sent whole for the backend to refuse, and the backend let it through at the step cap, unpaced.
- **A joint that reads past its travel starts its ramp at the edge of it.** The servo clamps
  every goal to the travel its calibration wrote into it, so such a joint first rises to that
  edge at the servo's own speed, whatever quackd sends, and is paced from there. That first
  stretch is the one part of a move `duration_s` cannot slow down: support the arm or place it
  inside its travel if that matters ([A pose past the travel](#a-pose-past-the-travel)).
- **`gripper` is not ramped.** It sends open or shut, which each backend maps to 100 or 0, and
  closes at the cap. A gripper named in `move_joints` is a joint with a goal and ramps like one.

Arrival and stalls are judged only once the goal itself is going out. A slow ramp moves a joint
less per tick than the stall rule's threshold, so a stall counted during the ramp would fail
every slow move. The price is that a joint blocked partway is found when the ramp ends rather
than when it stopped, pushing meanwhile against a goal one step ahead of it, as it always did.
The verb then fails naming the joint, where it stopped and its goal, and holds the arm. It
gives the move the time asked for, or the time the cap needs if that is longer, plus 2.5
seconds to settle, and never more than 18: the executor's own timeout for `move_joints` is 20,
and the verb ends first so that the reason names the joint that fell short.

Its datasheet, which the pilot is shown and told to judge a task against before anything moves ([manifest-spec.md](../manifest-spec.md)):

| | |
|---|---|
| Height | 0.53 m (estimate: one vendor's listing; reaching straight up) |
| Actuated joints | 6 (official: the LeRobot SO-101 docs; five joints and a gripper) |
| Payload | 0.5 kg (estimate: one vendor's listing) |
| Reach | 0.4 m (estimate: the maker's URDF, TheRobotStudio/SO-ARM100 Simulation/SO101/so101_new_calib.urdf; link lengths from the shoulder to the gripper frame, summed with the arm straight and rounded down) |
| Not published | mass |

And what it cannot do whatever the task says, which is the half a refusal usually turns on, in the words the pilot is shown:

- go anywhere: it is bolted to a table and has no base
- lift or hold more than about half a kilogram: a pen, an empty cup or a wooden block weighs
  far less than that, and a full bottle or a tool may weigh more
- reach anything more than about 0.4 m from its shoulder: that is the arm held straight out,
  and any bent pose reaches less
- feel what it holds: nothing reports grip force, so holding is inferred from the gripper
  stopping short of shut, which an empty hand that binds also does
- know its own mass: vendor listings disagree by a factor of three

A figure nobody published is listed as not published, and the pilot is told to answer `uncertain` and name it, rather than guess, where a task turns on it. A `.duck` file can correct any of it for the build in front of you ([duck-spec.md](../duck-spec.md)).

**Where the reach comes from.** Nobody publishes a reach for the SO-101, and up to 0.13.0 the sheet said so, which told the pilot to decline whatever turned on reaching: every task an arm has. The maker's URDF gives every link, so the figure is quackd's arithmetic on the maker's file. From the `shoulder_lift` joint outwards the joint origins are 0.116 m to `elbow_flex`, 0.135 m to `wrist_flex`, 0.064 m to `wrist_roll` and 0.098 m to the gripper frame, 0.413 m in all, which is an upper bound because links only add up in full when they are in a line. A grid sweep of the elbow and both wrist joints through their URDF limits puts the farthest the gripper frame gets from the shoulder axis at about 0.41 m. The sheet says 0.4, as an estimate, and the adapter's source (`REACH` in `quackd_lerobot/__init__.py`) keeps the four vectors so anyone can check them. It is measured from the shoulder joint rather than the base, and to the gripper frame rather than the fingertips.

**What the pilot is told about a pen.** The payload line used to end "and nothing whose weight is not known", which is nearly every object a task names: nobody tells the pilot what a pen weighs. It keeps the half kilogram, itself an estimate from one vendor's listing, and gives the pilot objects to judge by instead.

## Camera

No SO-101 has a camera in it. Whatever the kit's listing said, the arm is six servos and a
serial board, and every camera on one is a USB webcam that plugs into the *computer*: the
arm's own USB cable carries motor traffic and no video. So a camera here is a separate thing
you point quackd at, one of them or [several](#several-cameras).

```bash
uv run quackd doctor --robot lerobot:real --address COM5 --camera-url "opencv://0"
```

The index is OpenCV's, and `lerobot-find-cameras opencv` is what tells you which is which:
it lists every camera it can open and saves a frame from each under
`outputs/captured_images/`, so you can look at the pictures rather than guess. On a laptop
index 0 is usually the built-in webcam, so a plugged-in one is often 1 or 2. An index is a
scan position and not an identity: it can move when you replug or reboot.

| Key | Default | What it is |
|---|---|---|
| (the index) | required | `opencv://0`, or a device path, `opencv:///dev/video2` |
| `name` | `front` | what the frame is called in a policy's observation, in the label the model reads and in `frames/NNNN-<name>.png`. **Required on every url once you give more than one** |
| `width`, `height`, `fps` | the camera's own | a mode the camera cannot do is a refusal at connect, so these are worth setting only when you know it can. Width and height come together or not at all |
| `fourcc` | the camera's own | `MJPG` is the one worth asking for the moment there are two cameras: raw YUYV eats USB bandwidth |
| `rotation` | `0` | 90, 180 or 270, for a camera mounted sideways |
| `backend` | `any` | `msmf` or `dshow` on Windows, when a camera lists and then will not open |
| `fov` | unset | the lens's horizontal field of view in degrees, which is what bearings are computed from |

Anything else in the query is refused, with the shape, before LeRobot is even imported.

**The camera is quackd's, not the arm's.** LeRobot lets you give a follower its cameras, and
quackd deliberately does not: a follower's `is_connected` is the bus *and* every camera, and
`send_action` and `disconnect()` are gated on it, so one unplugged webcam would make every
move and every hold raise while the arm itself was perfectly fine. Beside the follower, a
camera that dies costs you `observe`, and a `pick` already running, and nothing else: the
heartbeat still reads the arm, the joints still move, `stop` still holds.

What that means at the bench:

- A camera you asked for and did not get is a **refusal at connect**, naming the url. You
  asked for it, and `doctor` gates its verdict on a real frame, so failing quietly would
  leave you believing you had eyes. The camera opens *before* the arm is touched, so a wrong
  index energises nothing and leaves nothing to undo; the arm connects without
  `--camera-url`.
- A camera that stops delivering later is **not** a refusal. `observe` fails with what the
  camera said (`the camera gave no frame: TimeoutError: ... too old`), and a `pick` running
  at that moment is stopped with the arm held, because the policy sees through it. The moving
  verbs carry on. `report_state` keeps working and gains a `CAMERA DOWN:` clause with the
  reason, and the health goes into the arm's state, which is what puts a dead webcam in the
  transcript on a run that has no `observe` to call. `quackd doctor` shows the same thing
  under `camera`. With several cameras a stall costs that camera's picture and nothing else,
  and both places name it: `CAMERA DOWN: side: ...` rather than a clause that does not say
  which eye closed, and one `camera <name>` row per camera in `doctor`.
- `observe` gives you bearings in the camera's own frame, and the detector behind it is an
  HSV threshold whose colour ranges are the *duck simulators'*. On a real desk it labels
  whatever happens to fall in one of those bands, `ball` for an orange thing and `person` for a
  blue one, and reports nothing when nothing does, so the label is a colour range's name rather
  than recognition. Distances assume the size of the duck simulators' ball, and without
  `?fov=` the bearing is uncalibrated and says so. Tuning the ranges to your own ball is in
  [the FAQ](../faq.md). What is honest whatever the detector makes of it is the frame itself,
  which a cloud model is shown every step unless you pass `--no-vision`.

### Several cameras

`--camera-url` repeats. This arm is the only body that reads more than one: every other one
refuses a second rather than opening the first and dropping the rest, and says who takes
several.

```bash
quackd robot add arm-01 lerobot:real --address COM5 \
    --camera-url "opencv://1?name=top" --camera-url "opencv://2?name=side"
```

```
✓ added arm-01: lerobot:real at COM5
  quackd run <duck> --robot arm-01
```

`quackd robot show arm-01` lists them back in the order they were given (the top of it):

```
name       arm-01
robot      lerobot:real
body       lerobot-so101 (arm, mobility none) 5 verbs: report_state, stop, move_joints, gripper,
           place
address    COM5
camera     opencv://1?name=top
           opencv://2?name=side
rest pose  -
```

The rules, all of them refusals rather than surprises:

| Rule | Why |
|---|---|
| with two or more, **every** url carries `?name=` | the name is the only thing telling the views apart, in the label on each picture the model reads, in a `pick` policy's observation dict, where every camera arrives under its own name, and in `frames/NNNN-<name>.png`. With one camera the name is optional, `front` is the default, and the frames are `frames/NNNN.png` |
| the names are unique | two views called `top` are two pictures a model cannot tell apart |
| an index appears once | two handles on one webcam is not two views, it is a camera that will not open twice |
| the **first** url is the primary | `--fov-deg` describes it, the `camera:` detections line reports it and nothing else, `observe` returns its frame, and the verbs that steer by sight read it alone. Those run at 10 Hz, and fetching every camera inside that loop would blow the deadman window. This arm has no such verb today, so here the rule is about the detections line and `observe` |

Everything else arrives at the model. Every frame reaches it each step, labelled with the
name of the camera that took it, on Claude, both OpenAI APIs, Gemini, and any
OpenAI-compatible local server with `--vision` on. The pilot is also told, in as many words,
which one is primary and that the `camera:` line describes that view and no other.

What happens when one of them dies mid-run depends on which one:

| Which camera stopped | What the model still gets |
|---|---|
| a secondary | every other view, and the `camera:` detections line unchanged. The failure is named in `report_state` and in `doctor`'s `camera <name>` row |
| the primary | the other views still arrive as pictures, and the detections line reports nothing seen. A bearing read off a different lens would point somewhere else, so quackd reports nothing rather than something from the wrong camera |

Either way the pictures that did arrive keep their names, on the wire and in
`frames/NNNN-<name>.png`. Whether a picture is named is decided by how many cameras the arm
has and never by how many answered this step, which matters most in exactly the case above: a
lone unnamed picture sitting under a detections line measured off the lens that died is the
one thing the naming exists to prevent, and it is also the case a count of the frames that
arrived cannot tell apart from a one-camera arm.

A second camera that will not open is a refusal **before the arm is energised**, and it lets
go of the first on the way out. Half a set of eyes nobody asked for is worse than the
refusal, because the frames would still arrive and look right.

> [!WARNING]
> Two uncompressed 640x480 streams on one USB controller can exceed its bandwidth. Both
> cameras open, and then one or both deliver nothing. `?fourcc=MJPG` on each is the answer,
> and a different physical USB controller for the second camera is the other one. This is an
> owner report rather than something measured here: the 2026-09-15 bench ran one webcam.

**The cost is pictures.** Each request carries the images from the last two exchanges, so two
cameras is four pictures per request where one camera is two, and that is what you pay in tokens
on every step of every run. On Claude Opus 5.5 and Fable 5.1, whose old frames are trimmed every
eight exchanges rather than on every one, it is up to eighteen where one camera is nine. It is
worth it for a wrist view plus an overhead view. It is not worth it for two views of the same
thing.

> [!NOTE]
> A local server, or a particular model behind one, may accept only one image per message.
> If it refuses a request with two, pass a single `--camera-url`.

`robots.json` stores a string for one camera and a list for several, so a registry file
written by 0.9 loads unchanged:

```json
"camera_url": ["opencv://1?name=top", "opencv://2?name=side"]
```

**What a `.duck` may ask of this arm, and where.** `quackd run` and `quackd validate` check
a task against the *static* manifest, before anything is connected, and that manifest claims
no camera for `lerobot:real` because nothing knows whether you plugged one in until the arm
has answered. Both `requires:` and the allowlist are checked against it, the allowlist as a
weaker line, so a task that so much as **allows** `observe` is refused before the arm is
touched:

```
x error: lerobot-lookout cannot run on lerobot:real: observe is not provided by arm-01
```

That is why `lerobot-lookout` asks for `report_state` instead, and on an arm you have not
driven before that is the better question anyway: whether it answers at all, whether torque is
on, and how warm it is. It is what ran first on the bench on 2026-09-15, and it is what ran
again with `--llm fake` to separate the arm from the model.

Over MCP it is the other way, and better. `robot_load_duckfile` validates against the manifest
of the robot **already connected**, so the same task loads cleanly on a session started with
`--camera-url` and is refused on one without. If you want a contract that allows `observe` on
this arm, that is where it works today.

**The pilot sees the camera whether or not `observe` is allowed.** Every step, the run loop
takes a frame from every camera, runs the detector over the primary's, and the detections go
into the observation the model reads. On a provider that accepts pictures the frames
themselves are attached as well, each labelled with its camera's name, which is `--vision`, on
by default for cloud models and off for local ones. What `observe` adds is the ability to *ask*
for a look as a deliberate act and get the frame back as a verb result, which is what
`robot_observe` does over MCP.

## Driving it

Three ways, and they differ only in who chooses the verbs.

**A task file**, which is the one with guard rails. The `.duck` names the allowlist, the
budgets and the success test, and quackd enforces all three:

```bash
quackd run lerobot-lookout --robot lerobot:real --address /dev/ttyACM0 --llm anthropic
```

`lerobot-lookout` ships with quackd and moves no joint: it reads the arm back and says what
it found. It is the first thing to point at a real arm. Writing your own is a file and a
`requires:` line, and `quackd validate <file> --robot lerobot:real` refuses it before a run
if this arm does not provide a verb it asks for.

**A one-line goal**, for when you want a single verb and there is no file for it. The model
still has to pass `assess_task`, and everything else still applies:

```bash
quackd run --goal "roll the wrist ten degrees and stop" --robot lerobot:real \
  --address /dev/ttyACM0 --llm anthropic --max-steps 3
```

Keep `--max-steps` small. `--llm fake` will not do here: the scripted pilot answers a
free-form goal with a fixed script that ignores it.

**From an MCP client**, which is you choosing each verb with the model doing the talking.
This is the only way to call `observe` on a real arm today, and the only way to run exactly
one verb and stop:

```json
{
  "mcpServers": {
    "arm": {
      "command": "uvx",
      "args": ["--from", "quackd[lerobot]", "quackd", "serve-mcp", "--robot", "lerobot:real",
               "--address", "COM5", "--camera-url", "opencv://1"]
    }
  }
}
```

Nine `robot_*` tools appear. `robot_list_verbs` first, then `robot_assess_task` with a
verdict, which `robot_run_verb` requires before anything that moves the body, then
`robot_run_verb(verb="move_joints", params={...})`. Both clients, the full tool list and a
two-minute script: [mcp.md](../mcp.md). The same path at walking pace, from an empty laptop to
a waving arm in fifteen steps, is
[Part 2 of the first run](../lerobot-first-run.md#part-2-from-claude-over-mcp).

A session started with a registered name that has a rest pose parks the arm at both ends, the
same as a run does, and **refuses to start** if it cannot reach that pose. The spec form above
has none, because a rest pose lives under a registered name, so that session parks nothing and
the arm goes limp when it ends, as it does at the end of both runs above. Register the arm
(`quackd robot add`, then `quackd robot rest-pose`) and name it instead, `"--robot", "arm-01"`
with no `--address`, to have it parked. Repeat `--camera-url` here too, and the session reads
every camera you name.

**A picture that comes with the task** is `--image PATH`, repeatable, and it is the flag that
makes "draw what is in the picture" a sentence this body can be given. This arm is the body a
task like that runs on: it is the one that holds a pen, and a drawing is a thing you show
somebody rather than describe. The picture is not a camera frame and is not treated like one:
it rides on the pilot's **first** turn labelled `task picture <name>:`, it is never trimmed out
of the history the way old frames are, and it stays in front of the model for the whole run, so
a task about a sketch is still about that sketch twenty turns later. Where this arm also has a
camera, both go out together, task pictures first and then the frames, each named, so the model
can tell the drawing it is copying from the desk it is copying onto:

```bash
quackd run --goal "draw what is in the picture" --robot arm-01 --image sketch.png --llm anthropic
```

Every request line says what actually went out, so a picture that never arrived is something
you read in the transcript rather than infer from a bad drawing (captured with `--llm fake
--vision`, the one pilot here that takes a picture and needs no key):

```
   llm>    step 0: 1 messages (1 with image, 1 task picture) to fake scripted:goal
```

Six formats are accepted, PNG, JPEG, WebP, GIF, BMP and TIFF, and every one of them is
re-encoded to PNG on the way in, brought down to a longest edge of 1568 pixels and then, where
the encoded picture is still over 1.5 MB, shrunk again. The copy kept at
`runs/<id>/images/00-sketch.png` is therefore byte for byte what the model was sent rather
than the file it was derived from, and a multi-frame GIF or TIFF goes out as its first frame.
Two `--image` flags whose files share a basename are both numbered by their place in the
list, because two directories with a `sketch.png` in each would otherwise arrive under one
label and a task naming one of them would be ambiguous in exactly the way a label exists to
prevent. Both refusals are worth knowing before you write a task around the flag. A
pilot that does not take images is refused rather than handed the words without the picture,
because a model told to draw what is in a picture it never received will improvise something
and the only sign of why would be a line in a transcript nobody reads twice:

```
✗ error: fake scripted:goal does not take images, so it cannot be given 1 picture
  quackd list-models marks the models that take no frames; --vision overrides it where
the vendor does take them, and a local model needs --vision
```

And `--image` is refused with `--flock` or `--robots`, because one picture handed to several
bodies is a task to write as several runs rather than one, and dropping the flag quietly on
the way into a flock would be a task about a picture that never arrived.

### The rehearsal: `--dry-run`

`--dry-run` connects to the arm for real and sends it nothing. Read-only verbs actually run,
so `report_state` reads the servos and `observe` takes a frame; every verb that would move
something is printed and skipped:

```
[dry-run] would run move_joints({'positions': {'wrist_roll': 10.0}, 'duration_s': 2.0})
[dry-run] move_joints not sent
```

This is worth doing on a real arm before the first real run. It exercises the port, the
calibration, the temperature read, the model, the allowlist and the budgets, and proves
which verbs the model is going to reach for, with the arm standing still. The heartbeat is
live throughout, so a dry run also tells you whether the arm answers reliably. Two of the
bench's dry runs on 2026-09-15 ended early, and both endings were the rehearsal doing its job:
one on a heartbeat round trip that failed once and never again, one on a pilot that answered
`uncertain` and a human who said no.

A dry run **never moves the arm**, and that includes the rest move at either end. It also means
a dry run on an arm that is not at its recorded rest pose ends with torque left on, because
nothing drove it there. That is [the rest pose](#the-rest-pose)'s rule and not an exception to
it.

A dry run needs the arm and moves none of it. The other rehearsal needs no arm and moves all of
it, in a model: [the simulator](#the-simulator-lerobotmujoco), which `quackd preflight` runs a
task file on seed after seed.

### What `pick` needs, and what it does not have

`pick` and `manipulate` hand the whole arm to a learned policy, and both are confirm-gated for
that reason. On `lerobot:real` and `lerobot:mujoco` they are **in the manifest only when the run
names a policy server** with `--policy-url` ([below](#a-run-with-a-policy-server)), or when a
policy object is handed to the backend in Python. A checkpoint loads in
[a policy server of its own](#a-policy-in-a-process-of-its-own-quackd-policy-serve), and the arm
reaches it through `RemoteRunner`, the server's client. `real.py` still has `load_policy(path)`,
which would load one in the arm's own process and which nothing calls or has run: it is the
`LOAD_POLICY` row in [the policies' table](#the-policies-upstream-lerobot-061) below. Setting a
server up, on the laptop or on a rented GPU, is [policies.md](../policies.md).

`pick` runs its policy as a segment, a loop of its own on the arm's clock that reads the arm,
judges the reading, takes the policy's goal and sends it, and the verb waits for that loop to
end. A policy object with one `act` a call runs at 10 Hz, one `act` a tick, as `pick` always ran
it:

- **It ends as soon as something is held.** `holding` is judged on the loop's own reads, each
  tick, so the policy stops the moment a grasp settles and the next verb is not refused as
  `pick is running`. The policy is reset at the start of every pick, where it has a `reset()`,
  so a second pick does not play out the first one's queued actions.
- **A goal past the travel is clipped and counted, not refused.** A verb's goal there is
  refused, and a policy's is clipped to the edge and counted in `extras.range_clips`, because
  one a tick over should not abort a grasp ([ADR-0036](../adr/0036-what-the-arm-does-not-say.md)).
  Its actions are capped at the verbs' own speed ([below](#manipulate-and-the-loop-a-policy-runs-in)).
  A joint reading outside its travel is left out of every action, as a stop leaves it out.
- **It stops the policy and holds the arm** on a hot joint, torque off, a camera that gave no
  frame, a goal that is not a finite number or names no motor of this arm, a goal held past the
  travel for 1 s, 3 sends in a row that did not reach the arm, or a read the arm did not
  answer. Torque and temperature are read every 0.5 s rather than every tick. A pick refuses to
  start with a joint more than 2 degrees outside its travel, with the same reading and travel a
  take-hold would name.
- **A stop from anywhere ends it and says so.** The heartbeat's stop, a `stop` from an MCP
  client, and a verb refused while the pick runs, which stops the arm, all end the pick as
  `stopped:` with what stopped it. A stop that lands while the pick is still starting, before
  the policy has been asked for anything, keeps it from starting, and the pick is refused
  with the stop named. So does a stop still under way when the pick begins. A stop that lands
  while the loop is on the bus waits for that call to come back, no longer than the call's own
  deadline, and then holds the arm. The heartbeat and the executor's read before a verb wait
  for it the same way, rather than take the loop's own call for a bus that stopped answering.

The arm's state carries `extras.timing`: how long each bus call took, the wait for the bus
included, and each of a policy's ticks, as a count, a median, a 99th percentile and the
longest, measured on the wall's clock. A pick's result carries the same. Nothing acts on it,
and it is there for a bench session to say how fast this arm's bus and a policy's loop are.

### `manipulate`, and the loop a policy runs in

`manipulate(instruction)` has one parameter, the subtask in a few words, and hands the arm to
the policy for one segment. Its preconditions are `pick`'s, torque on and nothing hot, and it
is confirm-gated like `pick`. An instruction of nothing but blanks is refused on every
backend, the mock included, in the same words, and the verb takes one line of plain text no
longer than a task file may list, whether a task lists any or not. On the mock the segment
runs its time on the mock's clock, and a stop, a release, a rest move or the next segment ends
it sooner, while a verb that sends a goal meanwhile is refused, as on the arm.

- **A task file can hold it to its own words and its own time.** A v3 file's `policy` section
  ([duck-spec.md](../duck-spec.md#policy-v3)) lists the instructions `manipulate` may give, and
  the verb then takes those and no others. It sets `segment_s`, which the run tells the backend
  before the first segment (`set_segment_s`), and the executor's timeout for the verb becomes
  that plus 10 s. On the simulator it adds the wall time the clock stands still while the
  policy thinks, which it bounds from the latency the policy declares and one frame from each
  camera, timed at connect, and holds to ten minutes (`FROZEN_INFERENCE_MAX_S`). A policy whose
  rate the loop would refuse, or whose latency starves every segment before its first chunk,
  adds nothing, since its segments end at their start. And it sets `total_s`, the seconds of segments the run may spend,
  `pick`'s among them, after which the next `pick` or `manipulate` is refused. A file that
  lists instructions cannot allow `pick` as well, since `pick` tells the policy a target of the
  pilot's own.
- **It runs for 10 s unless it ends sooner,** when the task file says nothing else. Its chunks
  played, or a policy that says it is done, end it, and so does the arm no longer moving under
  it: every joint within 0.5 degrees of where it was for 1 s of goals. That stall holds the
  arm where it stopped, because what stopped it may be in its way, and the policy's last goal
  would leave the servo pushing on.
  The verb then holds it again, as it does after every ending that held, since a bus can lose
  a hold's packet, and a stall whose last hold did not reach the arm is a failure that says
  so. Otherwise those three are a segment that ran, and the verb is ok on them and on nothing
  else. Its summary says why it ended, how long it ran, the chunks, the goals clipped to the
  travel and the ticks a second it achieved, and never that the task is done: nothing on this
  arm can tell, and the pilot judges it from a fresh look at the arm.
- **Anything else is a failure, with the arm held.** A policy that gives nothing to send, a
  guard from the list above, a policy that raises and a stop from anywhere end it that way, so
  the executor's rule for verbs that keep failing still stops a run that does.
- **The rate is the policy's own.** A policy declares its rate and where the number came from,
  and a rate that is not a finite number between 1 and 60 Hz refuses the segment before
  anything is sent. quackd never measures one.
- **The pace is the arm's clock.** Tick `k` is due at the segment's start plus `k` periods,
  counted from the tick's number rather than summed, so a tick that runs long costs only its
  own time. A tick that runs past the next one's deadline skips to the next whole period,
  counts what it skipped, and never sends twice in one period. Nor does a tick start before
  its deadline, on the simulator's clock either, which wakes only on whole steps of its own.
- **The speed cap is the verbs'.** A verb moves a joint at most `limits.step_deg` a tick of
  0.1 s, and a policy moves it no faster: per send that speed over the policy's rate, and never
  more than one verb step, so `QUACKD_LEROBOT_MAX_STEP_DEG` governs both. At the default 5
  degrees a policy at 30 Hz is capped at 1.7 degrees a send. The cap is written on the
  follower for the segment and put back to the verbs' step when the segment ends, however it
  ends, and written again before every stop, rest move and verb's goal.
- **A chunk replaces what was queued.** A policy that answers with a chunk of goals, one a tick
  from the tick its observation was read at, has the goals for ticks already played dropped
  when it arrives, and the rest replaces what was queued rather than being added after it. It
  is asked again once what is queued is down to half the chunk, or to twice the ticks its
  declared latency comes to where that is more, so any answer within half a chunk lands with
  something still queued, and never while it has not answered. The next can be asked for no
  sooner than the last lands, so a chunk has to last while it lands and while the next one
  does, twice the latency, and `serve` refuses a latency past half a chunk (below). A tick with
  nothing queued sends nothing, the arm holding its last goal, and 1 s of that ends the
  segment, with 5 s of grace for the first chunk.
- **The policy has a thread of its own.** Every call on it runs on a worker of its own, never
  on the threads the bus's calls use, so a policy that stops answering cannot hold up a read,
  a stop or the heartbeat. A segment starts only once the policy has finished whatever the last
  segment left it doing, waiting up to 5 s and refusing after, and an answer from an earlier
  segment is thrown away.
- **On the simulator** time stands still while the policy thinks, and its answer is taken in
  the policy's declared latency after it was asked for, which is where it would have landed on
  the arm, and only then judged, so an answer thrown away and an error land there too. A
  rehearsal plays the trajectory the arm would, and proves nothing about a rate.
- **A policy asked every tick** is one that carries state from tick to tick. A tick it never
  saw, skipped by the pacer or not answered before the next, ends the segment with the arm held
  and resets the policy. It is asked again only once it has answered, so it has to answer
  within a tick, and one that declares a longer latency is refused before anything is sent,
  on the simulator as on the arm.

### A policy in a process of its own: `quackd policy serve`

A checkpoint's processors are code: loading one imports whatever class its JSON names
(`PROCESSOR_CLASS_IMPORT` in [the policies' table](#the-policies-upstream-lerobot-061)). So no
quackd command loads a checkpoint in the process that owns the arm's serial bus
([ADR-0048](../adr/0048-policies-are-the-arms-executor.md)), and [policies.md](../policies.md)
is how to set one up, with the licences of the ones there are. The one thing in quackd that
would is `load_policy()`, an older Python helper that nothing in quackd calls (`LOAD_POLICY`,
in the same table). The policy runs in a server you start, in a terminal of its own, on the
laptop or on a rented GPU you reach through `ssh -L`, and the arm's side reaches it over HTTP on
port 9875, with a client that needs no torch and no LeRobot.
It serves a LeRobot checkpoint named as `REPO@REVISION`
([below](#serving-a-checkpoint)), and two scripted policies that need no torch either:
`scripted:hold` holds the arm where it reads, so a `manipulate` of it ends on a stall, and
`scripted:sweep` swings `wrist_flex` 5 degrees either side of where it started, one swing every
2 s. `quackd run`, `quackd preflight` and `quackd serve-mcp` point the arm at a server with
`--policy-url` ([below](#a-run-with-a-policy-server)), and `quackd policy check` asks one what it
serves.

```bash
quackd policy serve --policy scripted:sweep
```

It prints where it serves, the token file it wrote or read and the rate, and serves until
Ctrl+C. In a second terminal on the same machine:

```bash
quackd policy check --policy-url http://127.0.0.1:9875 --bench --seconds 3
```

```text
  http://127.0.0.1:9875
policy           scripted:sweep (quackd-policy 1, quackd 0.16.1)
features         whatever the arm has (a scripted policy)
rate             10 Hz, from scripted:sweep's own, the verbs' tick
chunks           10 actions, 10 played from each
latency          0 s declared
gpu              no
threads          not set
frames           raw
cameras          none mapped
state q01..q99   not reported
action q01..q99  not reported
loaded           nothing, a scripted policy loads no repository
achieved    10.0 Hz of 10: 30 of the 30 ticks in 3.0 s sent an action
starved     0 ticks with nothing to send
round trip  median 2.5 ms, p99 3.0 ms, max 3.0 ms over 6 requests
inference   median 0.1 ms on the server
latency     3.0 ms or less for 95% of the 7 steps timed, from the request to
            its chunk back: serve with --latency-s 0.01, so the simulator holds
            each chunk back as long, and bench again with it
```

`--bench` first times one warm step on its own, the whole wait for a chunk, both ways of the
wire and the inference. Then it streams synthetic observations through the real client at the
policy's rate, paced and queued as a segment is, for 10 s unless `--seconds` says otherwise, and
says the `--latency-s` to give: the 95th percentile of every step it timed, the warm one and the
stream's (`LATENCY_QUANTILE`), rounded up to the hundredth of a second. One step timed on its
own is one draw, and two benches of one checkpoint on one laptop suggested latencies too far
apart to serve with either. A tick the pacer skipped sent nothing, as a starved one did, and is
not counted as starved: with no `--latency-s` declared, a segment waits for each chunk in the
tick that asked for it, and the ticks that pass meanwhile are skipped, so bench again served
with the latency the first bench suggests
([policies.md](../policies.md#on-the-laptop-alone) has both benches of a trained ACT, and what a
good second one looks like). A policy whose steps take longer than a segment waits for its first
chunk, or longer than half a chunk's actions, rounded down, take to play (`latency_too_long`),
is given no latency, since `serve` would refuse any that covered them: the bench says it answers
too slowly to drive an arm from that machine, and to serve it on a GPU. `--bench` is one of the
two places a rate is measured, the other being the bench with the arm.
`quackd policy check --policy NAME` serves the policy in its own process for the length of the
check, with a token that lives only that long.

`--latency-s` declares how long the policy takes to answer a step, which the simulator holds
each chunk back by, and `--bench` is where the number comes from. `serve` and `check` refuse one
of 5 s or more, since a segment gives up waiting for its first chunk after that, and one longer
than half a chunk's actions, rounded down, take to play. A segment asks for the next chunk only
once the last has landed, so past half a chunk the arm has nothing to play for part of every
chunk, on the simulator as on the arm, and as long as a chunk every chunk lands after its last
action's tick and none plays. Each refusal says the longest latency the policy's chunk allows.
`check` of a server started with such a latency by an earlier quackd says so in its latency row,
its bench says so rather than that the latency covers what it timed, and the arm refuses to
connect to it, before any torque ([below](#whether-the-policy-fits-the-arm)). Where the latency
a server declares covers what a bench timed but the slowest step took longer than half a chunk,
the bench says a step that slow can leave the arm holding still, since no answer later than that
is sure to land before the queue runs out.

- **A token, always.** With no `--token-file` the server writes one to `~/.quackd/policy.token`
  the first time, readable by you alone where the OS allows, and reads it after that. The
  client reads `--policy-token`, then `QUACKD_POLICY_TOKEN`, then that file. It rides in one
  header, never in a URL, and is compared in constant time. Both ends refuse a token shorter
  than 16 characters, or with a space or a line break inside it, and say where it came from
  without quoting it: `openssl rand -hex 32` makes one.
- **Loopback, unless you say otherwise.** The server binds `127.0.0.1`, or `::1` when told, and
  refuses any other address, the rest of 127/8 included, unless `--behind-tls` says a TLS proxy
  stands in front of it. The client sends plain HTTP only to `127.0.0.1` and `::1`, and refuses
  `localhost` with a sentence saying to write `127.0.0.1`, because looking that name up costs
  about two seconds a call on some machines. A server on another machine is reached through
  `ssh -L 9875:127.0.0.1:9875`, or over `https://` with its certificate verified. It follows no
  proxy and no redirect.
- **Frames travel raw on loopback** and as JPEG at `--jpeg-quality` when the server asks for it,
  which one behind TLS does at 90 unless told otherwise. A tunnel looks like loopback to both
  ends, so give a server you reach through `ssh -L` a `--jpeg-quality`.
- **Every number is checked on both sides.** A `NaN` or an infinity, in JSON or out of a
  policy, is refused before it goes anywhere, and so is a body, a reply or a chunk past its
  bound. What a server says about itself in words, the policy, its version and where its rate
  came from, is printable ASCII or refused, so nothing `check` prints can move your terminal's
  cursor or write to its clipboard.
- **One session at a time.** Each segment's reset starts a session in place of the client's
  last, and a step for an ended session is refused. A reset from another client, such as
  `check --bench` against a server an arm is driving through, is refused while the arm's
  session is in use, so it never ends the arm's segment. A session is in use until it has been
  quiet for one chunk's span and the 5 s a first chunk may take, 6 s for the scripted
  policies, or until its client closes it, which it does when it is done. A reset whose reply
  never reached its client still leaves the session to that client, whose next reset or close
  is served at once. A step the client sends again, when a kept-alive connection turns out
  closed, is answered from the first answer rather than inferred twice.
- **A server that stops answering ends the segment with the arm held.** On the arm the client
  waits longer for a step than the loop's patience, so the loop ends the segment starved and
  says so, and `manipulate` fails. On the simulator the loop's patience is on the simulator's
  clock, which stands still while it waits for a chunk, so the client's own deadline, 10 s of
  the wall's, ends it instead, and the segment says the policy raised `PolicyServerError`. A
  reply that trickles in is cut off at the same deadline, however slowly each byte comes.
- **A slow step holds up nothing but the next step.** A reset is answered at once, even while a
  step its client gave up waiting for is still inferring: the policy's own reset waits for that
  step and runs before the new session's first one, and the old step's chunk is dropped as it
  ends. Another client's reset is refused only until that step has outlived its client's
  patience (`ABANDONED_S`, the client's 10 s), since nobody waits for it after that, and the
  refusal says how long it has been inferring and when to try again. Stopping the server
  refuses every request after it, a step waiting its turn behind the slow one too, and waits a
  second at most for a step still inferring (`CLOSE_WAIT_S`), since a step on a CPU can take
  minutes.

### Serving a checkpoint

A LeRobot checkpoint is served by name and revision, in a Python 3.12 environment with the
server's extra, which is LeRobot, torch and transformers and nothing the arm's own process
imports:

```bash
uv pip install "quackd[lerobot-vla]"
quackd policy serve --policy OWNER/NAME@REVISION --fps 30
```

It serves ACT, SmolVLA and pi05 checkpoints, the three whose processors quackd has read at
LeRobot 0.6.1. CI's `policy` job builds a tiny random ACT, puts it in a Hub cache of its own and
serves it offline through the real server to the real client and an arm behind it
(`POLICY_PIPELINE` in [the policies' table](#the-policies-upstream-lerobot-061)). On 2026-09-28 a
trained ACT from the Hub, `natsuki0000/act-so101-bluecap` at commit
`82f75fe40a311026b4f7cacdea7bf14cadc44ccd`, was served on a laptop's CPU and drove a twin of the
lab's arm on the simulator, and `lerobot/smolvla_base` loaded there and took minutes a chunk, so
it never answered a step through the client ([policies.md](../policies.md#smolvla-and-act)).
pi05 has not run (`VLA_PIPELINE`). Pi0.5 also wants LeRobot's `[pi]` extra and a Hub token that
has been granted its gated tokenizer.

Loading one reads before it builds, and refuses rather than guesses:

- **The JSON first.** `config.json` and both processor JSONs are fetched at the revision named,
  and nothing else of the repository. A policy type other than those three is refused, so is a
  feature that is neither the state, an image nor the action, and so is a processor step named
  by a `class` key or by any registry name outside the ones those three policies' processors
  use. A step named by class is imported from wherever it says, which is code the checkpoint
  chose, so the weights are never fetched for one.
- **No repository's own code.** A step that could trust a repository's code is told not to. A
  model a checkpoint names inside itself, SmolVLA's backbone or a tokenizer, would load at
  whatever revision the Hub has that day, so it is refused unless `--pin REPO@REVISION` names
  the revision, once per model. That is a whole commit, or a tag the Hub says is one, and never
  a branch or a pull request, which name whatever was last pushed to them. A SmolVLA whose
  `config.json` names no backbone is refused as well, since LeRobot would fill in a default of
  its own at no revision. The pinned model is fetched with its configs, tokenizer files and
  safetensors and no `.py` and no pickle, and handed over as that directory. The Hub's cache
  keeps one directory per commit, holding whatever was fetched at that commit before, so a
  pinned model whose directory holds any other kind of file, or whose configs map a class to
  code of their own (`auto_map`), is refused, with a sentence saying to point `HF_HUB_CACHE`
  at a fresh cache.
- **The rate.** No checkpoint carries one (`CONFIG_HAS_NO_RATE`), so it is `--fps`, or else the
  fps in `meta/info.json` of the dataset `train_config.json` names, at the revision it names.
  That revision is the checkpoint's choice, so it is taken only when it is a whole commit, or a
  tag the Hub says is one, which it cannot say while `HF_HUB_OFFLINE` is set. Any other
  revision, none, or no `train_config.json` refuses the server with a sentence saying to give
  `--fps`.
- **Then the weights,** the processors' state and the pinned models, and the build on the
  device LeRobot picks, a GPU where there is one. Every weight in `model.safetensors` is loaded
  into the model and none is left as it was built, or the server refuses to start: LeRobot's
  loader only logs a weight it did not find, and pi05's hands back a random network when its
  weights do not load, so quackd asks the one to be strict and loads the other's itself. An
  ACT's ImageNet backbone is never fetched, since the checkpoint's own weights replace it, and
  torch gets one thread fewer than it would take, unless `--threads` says how many. An ACT
  that ensembles its chunks over time is asked every tick and is refused without a GPU
  (`TICK_MODE`). A pi05 that learned relative actions is not: its whole chunk is made absolute
  against the state it was predicted from, which is how its training made it relative, where
  LeRobot's own loop makes each action absolute against the state of the tick it is played
  at (`VLA_PIPELINE`).

Each camera the arm has is the image of its own name, `observation.images.front` from the camera
called `front`, unless `--cameras NAME=KEY` maps it, and a key that is not one of the
checkpoint's images is refused. A step goes through LeRobot's own loop: the arm's reading, named
by its bus's motors in the bus's order, through `build_inference_frame` and the pre-processor,
the chunk from `predict_action_chunk` cut to `n_action_steps`, the post-processor over the whole
chunk, and each row a goal per motor. One lock holds the three together, and every session's
reset resets the policy and both processors. `check` says all of it, here of the tiny random
ACT CI's job builds, put in a Hub cache on a laptop's CPU the way that job puts it, with
`HF_HUB_OFFLINE=1` and `HF_HUB_CACHE` pointing at it:

```bash
quackd policy check --policy quackd-test/tiny-act@v1 --bench --seconds 3
```

```text
  served here for the check, at http://127.0.0.1:53804
policy           quackd-test/tiny-act@v1 (quackd-policy 1, quackd 0.16.1)
features         state 6, action 6, images observation.images.front 64x48
rate             10 Hz, from
                 quackd-test/tiny-data@ed2440c0bf574309f37e0a639e02d7b34cb2939c
                 meta/info.json
chunks           8 actions, 4 played from each
latency          0 s declared
gpu              no
threads          3
frames           raw
cameras          front=observation.images.front
state q01..q99   -32.4835..32.4835, -47.5165..47.5165, 25..75,
                 -42.5055..42.5055, -62.5055..62.5055, -37.4945..37.4945
action q01..q99  -32.4835..32.4835, -47.5165..47.5165, 25..75,
                 -42.5055..42.5055, -62.5055..62.5055, -37.4945..37.4945
loaded           checkpoint quackd-test/tiny-act@v1, commit 0b812cabb86d;
                 dataset
                 quackd-test/tiny-data@ed2440c0bf574309f37e0a639e02d7b34cb2939c
                 , its fps
achieved    10.0 Hz of 10: 30 of the 30 ticks in 3.0 s sent an action
starved     0 ticks with nothing to send
round trip  median 21.6 ms, p99 70.6 ms, max 70.6 ms over 15 requests
inference   median 19.7 ms on the server
latency     70.6 ms or less for 95% of the 16 steps timed, from the request to
            its chunk back: serve with --latency-s 0.08, so the simulator holds
            each chunk back as long, and bench again with it
```

Every repository the server loaded, with the revision it loaded it at and, for a tag or a
branch, the commit that was that day, is in `/v1/policy`, and the arm's connect writes it into
the run's record. LeRobot says a line or two of its own as it loads, about the device and the
weights, before any of this.

### Whether the policy fits the arm

The arm asks the server what it serves as it connects, once its cameras are open and before
any motor is energised, and a policy that could not drive it refuses the connect, with the arm
never touched. Every limit is read and none is typed: the motors are the bus's, in its order,
each camera's size is a frame it just gave, and the travel is the calibration file's.

- **The state and the action** are as long as the bus has motors. Where a checkpoint names its
  action's dimensions, as a pi05 does, they are the bus's motors in the bus's order. Where it
  does not, the order is trusted and not checked: the policy is taken to have learned from a
  bus that lists its motors as this one does, which is how LeRobot records one.
- **The cameras.** An ACT needs a camera for every image it looks at. A SmolVLA or a pi05 runs
  with some of its images missing, and the ones that are go into the record as padded.
- **A frame of another size** than the checkpoint's image is refused, with the size to give the
  camera with `--camera-url`'s `width=` and `height=`, which the real arm's cameras and the
  simulator's both take.
- **The frame of reference.** The 1st and 99th percentiles of the state the policy learned from
  have to lie inside this arm's calibrated travel, 2 degrees of slack included, the same slack
  a reading gets everywhere else. A policy trained on an arm calibrated another way asks for
  goals that pin this one at its limits, so it is refused, naming each joint with its
  percentiles and its travel. A checkpoint that reports no percentiles is let through, and the
  record says it was not checked.
- **The latency** the server declares. `quackd policy serve` refuses one longer than half a
  chunk's actions, rounded down, take to play
  ([above](#a-policy-in-a-process-of-its-own-quackd-policy-serve)), and a server an earlier
  quackd started may still declare one. The connect refuses it, saying how many ticks of every
  chunk the arm would have nothing to play for and the longest `--latency-s` to start the server
  again with. Nothing overrides it.

**`--accept-other-frame`** overrides the frame of reference, on `quackd run`, `quackd preflight`
and `quackd serve-mcp`, beside `--policy-url`, and is refused without it. It lets a policy
learned on an arm calibrated another way connect, and nothing else: every goal it answers is
still clipped to this arm's calibrated travel before it is sent, so it changes what drives the
arm and never where the arm may go. Give it when you know the two arms' frames match. The
connect's note says the percentiles were accepted, and the `policy` block of the run's
`summary.json` and `run_start` carry `accept_other_frame`. On the lab arm's calibration, 55 of
the 68 servable SO-100 and SO-101 ACT checkpoints on the Hub were refused on `shoulder_lift`,
whose recorded travel there does not reach the arm's own fold
([ADR-0045](../adr/0045-a-rest-pose-the-calibration-cannot-reach.md)) while theirs did. A frame of
another size can be accepted from Python alone, with `RemoteRunner`'s `accept_frame_size=True`,
and the record says when it was.

The check is made again as every segment starts, on what the server says then. The arm's
process lives as long as a pilot's session, and a server can be started again on the same
port and token in that time. A policy other than the one the connect checked starts no
segment, with a sentence saying to connect the arm again so the new one is checked and named
in the record, and the same policy starts none once it no longer fits.

### A run with a policy server

`--policy-url` hands the arm's `pick` and `manipulate` to a server that is running, on
`quackd run`, `quackd preflight` and `quackd serve-mcp`:

```bash
quackd run --goal "put the block in the bowl" --robot lerobot:mujoco --policy-url http://127.0.0.1:9875
```

- **Only the flag names it.** No variable and no registered robot carries the address, so a
  policy drives the arm only on a command that says so. The token is `--policy-token`, else
  `QUACKD_POLICY_TOKEN`, else the file `quackd policy serve` wrote, and an address the client
  refuses, or a token found nowhere, is refused as the arm is built, before anything connects.
  A task that allows `pick` or `manipulate` on the arm, run without the flag, is refused with a
  line saying to start a server and give the command its address.
- **One arm.** `lerobot:real` and `lerobot:mujoco` take it. The mock runs its own scripted
  policy and refuses one, every other body refuses it naming these two, and a flock refuses it,
  because one server drives one arm.
- **Asked before the arm connects.** The server is asked what it serves before anything
  connects, so a server that is not there is one sentence with nothing energised, and the run
  header names the server and its checkpoint. The connect then checks the policy against the
  arm before any torque ([above](#whether-the-policy-fits-the-arm)).
- **A goal run asks at a terminal before each segment.** A `--goal` allows only verbs that are
  safe, and `manipulate` is not one. With a policy server it is allowed all the same, behind a
  confirm, so a person at a terminal is asked before each segment, and without one a goal is
  what it was. Under `--yes`, or with a pipe or file on stdin, a segment starts without
  anybody being asked, as any other gated verb does
  ([safety.md](../safety.md#who-the-record-says-was-asked)), and `--controller vla` refuses
  both.
- **The pilot is told what executes.** A run whose verbs include `manipulate` has a
  `Your executor` section in its prompt: hand the policy one short subtask per call, look again
  after each, and never read the verb's ok as the subtask done. With a camera and a pilot that
  can see, the look is the frame the next observation brings, and otherwise a reading of the
  arm with `report_state`, which cannot show where a block went.
- **The record keeps what the policy did.** `run_start` names the server and the checkpoint,
  and `summary.json` has a `policy` block: the server, the policy, its rate, every repository
  its server loaded at the revision it loaded, the JPEG quality, the segments and their
  seconds, ticks, late ticks, chunks, starved ticks and clipped goals, the rate the ticks were
  achieved at, and the mean and longest round trip to the server. The seconds and the rate are
  on the arm's clock, which the ticks were paced on, and on the simulator that clock is the
  simulator's own and runs as fast as it steps, so the block says `clock: sim` there and keeps
  the segments' seconds on the wall's clock beside them (`wall_s`). The line under the verdict
  says the same in one counter, and the time it splits gains the policy's wall seconds. No
  action goes into a verb's result or the record.
- **Over MCP** both verbs are confirm gated, so a client reaches them only on a server started
  with `--yes`.

### A scripted pilot that a person judges: `--controller vla`

`--controller llm`, the default, is the model `--llm` names deciding each subtask. With
`--controller vla` there is no model at all. A scripted pilot hands the policy each instruction
the task file lists under `policy.instructions` (a `duck: 3` file,
[duck-spec.md](../duck-spec.md#policy-v3)), in order, one `manipulate` segment each, or the
`--goal` text as the only one:

```bash
quackd run stack-blocks.duck --robot lerobot:mujoco --policy-url http://127.0.0.1:9875 --controller vla
```

- **You are its verdict, twice.** Its `assess_task` is always `uncertain`, so you are asked
  before the first segment whether the arm should try, and after the last one
  `Did the arm do it?`, with the instructions it ran and the task file's `success` lines in
  front of you. Only your yes makes the run a success. The question and your answer are a
  `judge` prompt in the record
  ([safety.md](../safety.md#a-pilot-that-cannot-judge---controller-vla)). The time you take to
  look is not charged to `max_minutes`, and a prompt that ends without an answer is not a no:
  the run fails saying what the prompt raised, and no `judge` row is written.
- **A segment that does not end ok ends the list.** A confirm you decline, a guard, a starved
  policy: the pilot starts no other segment and fails the run in the verb's own words, without
  asking whether the task was done.
- **The task file's budget holds.** Each segment is a `manipulate` like any pilot's, through the
  same narrowed verb, confirm gate and `policy.total_s`. A budget that runs out before the last
  instruction ends the run on its budget, and you are still asked about the segments that ran.
- **It reads nothing but the verb's result and your answer.** The simulator's own truth about
  the table never reaches it: `quackd preflight` judges a rehearsal on its own.
- **It costs nothing.** No model is asked, so the counter line reads `tokens 0+0` and `cost $0`.
- **It is refused before anything connects** without `--policy-url`, with `--yes`, with no
  terminal to ask on, with `--dry-run`, beside `--decision-llm` or a decision LLM
  `QUACKD_DECISION_LLM` names, with a flag for a model or a picture it would ignore (`--llm`,
  `--base-url`, `--api-key`, `--extra-body`, `--vision`, `--image`), and for a task file whose
  `policy.instructions` is empty. A `--goal` has to be one short subtask, as a listed
  instruction is. `serve-mcp` refuses `--controller`, because over MCP the client is the pilot.

## The simulator: `lerobot:mujoco`

`lerobot:mujoco` is this arm's simulator, and it is the `real` backend's own code: the connect
and its retries, the travel read off a calibration and every refusal past it, the rest move,
the hold, the release and the take-hold, the close and what it says. What runs underneath is a
physics model of the SO-101 in [MuJoCo](https://github.com/google-deepmind/mujoco) instead of
LeRobot, the scene's cameras instead of webcams, and the world's clock instead of the wall's.
So a task file rehearsed here goes through the lines that will drive the arm in the lab, and
what it meets on the way, a travel it cannot reach, a bus that drops a packet, a pilot that
reaches for a verb nobody allowed, it meets at home.
[ADR-0047](../adr/0047-the-arms-simulator-runs-the-real-backend.md) is the reasoning.

![Two views of a simulated SO-101 arm in MuJoCo, side by side, under a strip naming the verb being run. Left, the arm on a grey table seen from in front and to one side, with a red cube and a dark pen lying in front of it: it starts with the upper arm upright and the forearm level, raises the whole arm on a diagonal, brings the forearm back down level with the upper arm nearly upright, then swings the arm from side to side at the shoulder three times and stops. Right, the scene's front camera, the view the model was sent: the raised arm runs off the top of the frame, then the arm held out level swings across it from one side to the other, pointing straight at the camera as it passes the middle.](../assets/lerobot-sim.gif)

*The sentence the real arm at the top of the README was given, `Wave to the camera with an
extended arm`, on a bare `--robot lerobot:mujoco` with the `front` camera, piloted by OpenAI's
`gpt-6-sol` and recorded by [`lerobot_sim.py`](../assets/lerobot_sim.py)
([how it was made](../assets/README.md)).*

### Running it

```bash
uv pip install "quackd[lerobot-sim]"
quackd run lerobot-lookout --robot lerobot:mujoco --llm fake
```

The first connect fetches the model, the maker's own, one file at a time at a pinned commit,
each checked against its sha256 ([its upstream](#the-simulators-upstream-so-arm100)), and says
so once:

```
fetching the SO-101 model (16 files, about 16 MB) from https://github.com/TheRobotStudio/SO-ARM100 into /home/you/.quackd/cache/so-arm100/5f6d2b876a53a4872e405b991dd925556c9e38a4. Apache-2.0, never shipped with quackd
```

A bare `--robot lerobot:mujoco` names no arm, so there is no calibration of yours to read, and
the connect says what it used instead:

```
-  note    no arm was named, so this rehearsal runs on the generic arm, whose travel is the model's own range on every joint and not any arm's calibration; give --address the file lerobot-calibrate wrote for an arm to rehearse that arm's travel
```

It never reads the file LeRobot keeps under `arm-01`, the id quackd gives an arm nobody named,
which on a machine that has calibrated an arm is somebody's real travel under a name this run
never gave. To rehearse your own arm, name its calibration: `--address` takes the file
`lerobot-calibrate` wrote, and a registered `lerobot:mujoco` robot with no address reads the one
LeRobot keeps under its name. An address shaped like a serial port is refused on its shape,
before anything opens it:

```
x error: lerobot mujoco: --address 'COM5' is a serial port, and the address of a lerobot:mujoco
robot is the calibration file the arm's runs read, never the arm's port, so nothing was opened
there. Give --address the file lerobot-calibrate wrote for the arm, which quackd robot twin finds
for a registered one, or leave it out to rehearse on the generic arm.
```

**The easy way is a twin of the arm you registered.** `quackd robot twin arm-01` registers
`arm-01-sim` on the calibration file `arm-01`'s runs read, with its rest pose, its pilot and
each camera the simulator can render:

```
$ quackd robot twin arm-01
+ added arm-01-sim: lerobot:mujoco, a simulator of arm-01 on
/home/you/.cache/huggingface/lerobot/calibration/robots/so_follower/arm-01.json
  copied from arm-01: rest pose, pilot openai:gpt-6-sol, 2 camera urls
! robots.json now holds a lerobot:mujoco robot, and quackd 0.14 and earlier cannot read the file at all: quackd robot remove arm-01-sim before going back to one
  quackd preflight <duck> --robot arm-01-sim
```

`--robot arm-01-sim` is then the same travel starting from the same fold, and its memory is its
own. The warning is the one cost, and [registry.md](../registry.md#a-simulator-of-an-arm) has
what `twin` copies and what it refuses. The simulated arm starts at the rest pose as recorded,
limited by the model's own stops and settled clear of its table and of itself where the pose
puts it into either (below), so the first rest move finds it already there. A joint recorded
past one of those stops starts at the stop, with a note, because nobody has read a real arm's
stops against the model's yet.

A fold can also put the model into its own table, or one of its links into the next, by more
than a millimetre. Started there, the first step of physics would throw the arm out of the
table, and it could never get back to a pose inside it, so every run that let time pass would
end with the rest move stalled short of it and torque left on. So the simulator settles it
first: the physics runs for a second before the clock starts, with every joint held, the
contacts push the arm clear, and each joint that moved takes where it came to rest, within the
model's stops, as its start, the goal it holds and the rest pose the close drives it back to.

The pose the close parks the arm in is settled the same way. A joint recorded past its travel
is parked at the edge of it ([A pose past the travel](#a-pose-past-the-travel)), and a fold that
starts clear can put the model into its table with that one joint at the edge, so every run
that moved the joint would end with the rest move stalled. So that pose is settled too, with
the joint driven from the fold to the edge at the rest move's pace, and each other joint takes
where it came to rest there as its start as well, within its travel: the arm starts where it can
be both folded and parked. A joint settled past its travel is parked at the edge of it and
judged there by the half-line rule, as a recorded pose past its travel is, so one the table
stops short of the edge, on the side of its fold, is at rest where it stops. The connect names the contacts and the joints, as it did for
the lab arm's twin, arm-01-sim:

```
·  note    the rest pose puts gripper 21 mm into the table, moving_jaw_so101_v1 13 mm into the table, lower_arm 10 mm into shoulder and wrist 3 mm into shoulder on the model, and with shoulder_lift at the edge of its travel, where the close's rest move parks the arm, gripper 10 mm into the table, moving_jaw_so101_v1 8 mm into the table, wrist 6 mm into shoulder, gripper 5 mm into shoulder and wrist_camera_mount 1 mm into shoulder, so the simulated arm starts and rests where it settles against them instead, with elbow_flex at 82.9 degrees in place of 96.4 and wrist_flex at 81.9 degrees in place of 72.2. The model's joint zeros and signs are an assumption (JOINT_ZERO, JOINT_SIGN) until the bench checks them, so the pose may be right on the arm and the model's frame wrong
```

The lab arm rests in that fold on its own bench, so the model differs from the arm somewhere: in
where its joints are zero or which way they turn, or in where the table meets its base. Only the
bench can say which, and [PLAN.md](../../PLAN.md) has it beside the joint zeros and signs.

A fold the settle cannot clear is refused at connect instead, naming what the pose put where
and what a second of settling left: a part held in against a stop, or pinned by a joint that
cannot move, stays in however long it settles, and an arm started there would stall on its
first move and every move after it. So is a fold whose parked pose no close could call at
rest: the settle there pushes the joint at the edge back into its travel, further than a reached
pose may miss by, or the angles it leaves put the start back in. Give the robot a rest pose the
model can start at
([below](#when-the-simulator-will-not-start)).

The settle judges those two poses and not every way back between them. After a move that takes
the arm off its fold, whether it lifts the arm upright, moves `shoulder_lift` within its travel
or drags the gripper across the table, the rest move can set the gripper down on the table
short of its settled angles. The table then holds the wrist or the elbow further from its goal
than a reached pose may miss by, and the close says the rest move stalled and keeps torque on.
Every such stall measured had the arm touching the table and nothing else.

A close that finds the simulated arm away from its rest pose keeps torque on, as the arm's own
does, and says there is nothing to hold, release or park, since the simulated arm ends with the
run. A run at a terminal is not offered the release a real arm's is, for the same reason.

`quackd doctor --robot arm-01-sim` connects it, which renders one small frame as every connect
does, so it tells you before any run whether this machine can draw the scene, and it says first
what it is:

```
! this is the arm's simulator: connecting takes torque off every simulated motor for a moment, as
LeRobot's connect does on a real arm, and there is no arm to support
```

### What it renders

The arm's own camera flags work here unchanged, because the simulator reads the same
`opencv://` urls with the same parser. The index is ignored, `?name=` picks one of the scene's
three mounts, `front`, `top` or `wrist`, and `width`, `height`, `rotation` and `fov` apply,
`fov` being the horizontal view in degrees, 90 unless you give one. `wrist` is rendered from the
wrist camera mount on upstream's model, which may not be where yours sits. `front` and `top` are
quackd's own views of the table and not where any real camera stands, and a connect note and
the state's assumptions both say so. A name the scene has no mount for is refused before
anything connects:

```
x error: lerobot mujoco: --camera-url 'opencv://1?name=side' names 'side', and the scene has no
camera by that name; its cameras are front, top, wrist. Name one with ?name=.
```

The pilot is told it is on a simulator, that every camera is a rendered view and that the
physics is the model's rather than anything measured on an SO-101, and a run given no camera is
told that whatever is on the table goes unseen. A run writes no GIF here, and the recording
above is a script's, which drives a run and films it from a tick hook on the simulator's clock.
`--live` opens MuJoCo's own viewer.

### Time, and what a seed repeats

The simulator's time moves only while something waits on it. Every sleep the real backend makes,
a verb's tick, the rest move's, the settle before a hold is read back, waits on the world's
clock, and the physics steps while every such wait is parked and stops the moment one of them
wakes. So a pilot's thinking costs no sim time, and a run's `max_minutes` counts the arm's time,
not yours. One step of that clock is 0.01 s, five of the model's physics steps. `--seed` lays
out the table, and under one seed the simulator does the same again on a run through
`quackd run` or `quackd preflight`, which make one call at a time, though a model may not choose
the same verbs twice. An MCP session runs tool calls at once, so it is not seeded. `--live` holds
each step until the wall has caught up, for a person watching, and it is the same lockstep clock,
so nothing timed on it is a rate.

### `--by-hand` on the simulator

`--by-hand` runs here as it does on the desk, up to the moment somebody would place the arm.
quackd releases it at its rest pose and asks you to place it, and nobody can. So when you press
Enter the take-hold first lets the limp arm fall for a second of sim time, and then takes hold
of whatever pose the table, the model's stops and its own weight have left it in, or refuses in
the arm's own words where a joint has fallen past its travel or moved under the hold
([Placing it by hand](#placing-it-by-hand)). Which of those happens is the model's physics, so
this rehearses what the take-hold says and does with whatever pose a fall leaves: the hold it
takes, or the refusal and the torque it leaves off. It does not say whether your arm's rest pose
holds it up, because how a released joint settles is the model's answer (`SERVO_DYNAMICS`),
and which way gravity pulls each joint rests on signs and zeros nobody has checked against an
arm (`JOINT_SIGN`, `JOINT_ZERO`). Only the bench gives the arm's.

### Rehearsing a task file: `quackd preflight`

`quackd preflight` is what the simulator is for. It rehearses task files on a simulator, never on
an arm, and refuses anything else before it builds it:

```
x error: arm-01 (lerobot:real) is not a simulator, and preflight runs only on
one, since it drives the robot through every task file seed after seed: quackd
robot twin NAME registers a lerobot:mujoco simulator of a registered arm to
rehearse on
  then quackd preflight FILES --robot NAME-sim
```

It also refuses to rehearse with a pilot nobody named, because the scripted one rehearses nothing
a model would do. The pilot is `--llm`, else the robot's registered one, else `QUACKD_LLM`, and
the scripted pilot runs only when typed as `--llm fake`. Each file is checked against the robot
as `validate` checks it, the simulator is connected and closed `--connect-cycles` times, and the
task is run `--seeds` times, three of each unless you say, with memory off:

```
$ quackd preflight lerobot-lookout --robot arm-01-sim --llm fake
quackd preflight lerobot-lookout
+--------------------------------------------------------------------+
| seed | outcome | steps | close            | checks | cost | result |
|------+---------+-------+------------------+--------+------+--------|
|    0 | success |     1 | at the rest pose |      - |   $0 | pass   |
|    1 | success |     1 | at the rest pose |      - |   $0 | pass   |
|    2 | success |     1 | at the rest pose |      - |   $0 | pass   |
+--------------------------------------------------------------------+
  no lerobot-lookout.sim.yaml beside it, so only the close was judged
model cost $0 over 3 runs
sim dt 0.01 s
+ 1 file passed preflight
```

A run passes when nothing escaped it, no call to the simulated bus was left hanging, its close
ended at the rest pose (or, on a robot with no rest pose, such as a bare `lerobot:mujoco`, found
none to return to, unless the sidecar asks `at_rest: true`), and every check in the task's
sidecar held. A close that missed the rest pose says how in the `close` column: the rest move
was refused, stalled, or ran out of time. The pilot's own verdict is in the `outcome` column and
is not one of those: a pilot that says it succeeded is what is being rehearsed, not the judge of
it. `--json` prints one object per file, and the command exits 1 unless every run passed.

`--faults SPEC` gives the connect cycles a seeded bus to meet: rates for `handshake`,
`configure`, `write`, `torque`, `torque_read` and `temperature_read`, and `read_loss_from=N` for
an arm that stops answering at its Nth read of the joints, as `handshake=0.2,configure=0.3`.
Each fault is raised in LeRobot's own words, so the connect retries it as it would on the arm,
and says so as it would:

```
WARNING  connect attempt 1 of 3 failed on wrist_flex (id 4): Failed to write 'Lock' on id_=4 with
         '1' after 1 tries. [TxRxResult] There is no status packet! The port was closed without a
         write to any motor, and connect runs again
```

A connect that gives up in words under a plan, as the arm's does once its retries are spent, is
noted rather than failed. The runs after the cycles meet no faults, so a robot that cannot
connect at all still fails every one of them.

### The sidecar

What a run of a task has to leave behind is written beside the task, in `<task>.sim.yaml`, and
never in its frontmatter, which an MCP pilot is handed whole: a pilot that can read what it will
be marked on is rehearsing the marking. A sidecar lays out the table and says what has to be so
when the run ends. This one is for a grasp task rehearsed on a twin, whose close has a rest pose
to reach:

```yaml
scene:
  objects:
    - {name: block, kind: box, size: [0.0125, 0.0125, 0.0125]}
checks:
  - at_rest: true
  - joint_moved: {joint: shoulder_lift, min_deg: 10}
  - lifted: {object: block, min_m: 0.02, when: peak}
  - moved: {object: block, min_m: 0.05, when: latched}
```

`scene` replaces the simulator's own cube and pen. An object is a `box`, three half sizes in
metres, or a `capsule`, its radius and half length, laid on the table where the seed puts it.
`place: jaws` lays it on the table between the gripper's fingers as the arm starts instead,
which needs a rest pose whose open jaws point down at the table around it: a close from there
has to bring the moving finger onto it, and the connect rehearses that close in the physics,
on a copy, as it lays the object out. On a robot whose arm starts anywhere else, the generic
arm included, the connect is refused ([below](#when-the-simulator-will-not-start)). `mass_kg`
and `rgba` are optional. `checks` are
`at_rest` (true: the close has to end at the rest pose, so a robot with none fails it, false:
the task leaves the arm where its rest move is refused, so a rest move that is made has to be
refused, since one that stalls or runs out of time fails the run whatever the sidecar says,
and a robot with no rest pose makes none and passes it),
`joint_moved`, `lifted` (off the table, touching the gripper and up by `min_m`) and `moved` (its
centre `min_m` from where it was laid). Every threshold is measured from the run's own start. A
joint check reads the transcript, the readings the pilot was shown. An object check reads the
simulator's truth, latched on the way into the stop that opens the teardown, before the rest
move carries the arm back through the scene (`latched`), or the most the object reached before
then (`peak`). That truth is kept on the transport and never in the state's extras, so neither
the pilot nor an MCP client can read it. A check that does not hold is named under the table:

```
  seed 0: moved block (latched): 0.000 m from where it was laid as the run ended, of 0.05 m asked
```

That one is `lerobot-lookout`, which moves nothing, given a sidecar that asked for a block to be
moved. A file with no sidecar is judged on its close alone, as above.

### What it proves, and what it does not

It proves that a task file survives the code that will run it: the connect and its retries on a
bus that drops packets, the travel of the arm you calibrated and every refusal at its edge, the
rest move at both ends, a pilot's choices against the allowlist and the budgets, the close, and
a grasp judged by where the object really went rather than by what the pilot said. The follower
under the backend plays what LeRobot and the servo do with a goal: LeRobot caps each send at the
step, the servo clamps the goal to the calibrated travel and leaves the reading alone, and a
limp joint drives to its last goal when torque returns, which is the worst case of an
assumption nobody has checked on an arm.

It does not prove that the arm can do the task. The dynamics are the model's, a calculation and
another robot's servo properties, not anything measured on an SO-101, so a grasp that holds here
is evidence about the model (`SERVO_DYNAMICS`, below). Which way each joint turns and where its
zero sits are assumed to match the model until a bench checks them (`JOINT_SIGN`, `JOINT_ZERO`).
The cameras' placement is quackd's, the servos never warm, and no rate measured here is the real
bus's. So `lerobot:mujoco`'s status never raises `lerobot:real`'s, and what only the bench can
settle is listed in [PLAN.md](../../PLAN.md).

### When the simulator will not start

| What you see | What it means | What to do |
|---|---|---|
| `adapter 'lerobot' needs an extra: uv pip install 'quackd[lerobot-sim]'` | MuJoCo is not installed here. The connect says so before anything is fetched | install the extra |
| `lerobot mujoco: --address 'COM5' is a serial port, ...` | the address of a simulated arm is a calibration file, never a port, and nothing was opened | give `--address` the calibration file, or use `quackd robot twin`, or leave it out for the generic arm |
| `lerobot mujoco: --camera-url '...' names '...', and the scene has no camera by that name; its cameras are front, top, wrist.` | a `?name=` the scene has no mount for | name `front`, `top` or `wrist` |
| `lerobot mujoco: the rest pose puts ... on the model, and 1 s of settling still leaves ..., so the simulated arm cannot start there` | the rest pose puts the model into its table or into itself, and a second of settling leaves a part more than a millimetre in, held there by a stop or by a joint that cannot move. An arm started there would stall on its first move | give the robot a rest pose the model can start at: `quackd robot rest-pose NAME` records the model's zero, where the simulated arm starts without one. A twin's rest pose is its own, so the arm it copies keeps its pose |
| `lerobot mujoco: with shoulder_lift at the edge of the travel its calibration recorded, where the close's rest move parks the arm, the rest pose puts ... on the model, and 1 s of settling leaves ..., so the simulated arm could not come back to rest` | with a joint recorded past its travel parked at the edge of it, as every close after a move of that joint parks it, the model is in its table or in itself, and a second of settling leaves that joint pushed back into its travel further than a reached pose may miss by, or the arm's start in the table or in itself | the same: give the robot a rest pose the model can start at, or calibrate again with the arm folded so the fold is inside the travel ([A pose past the travel](#a-pose-past-the-travel)) |
| `lerobot mujoco: the scene lays block between the jaws, and as the arm starts its fixed finger ends ... above the table, over the top of block, ...` | a sidecar placed an object between the jaws, and the rest pose holds the jaws above it | give the robot a rest pose whose open jaws point down at the table around the object, or lay the object on the table instead |
| `lerobot mujoco: the scene lays block between the jaws, and as the arm starts they are open narrower than block, ...` | the rest pose's jaws are shut, or open narrower than the object, which would start inside a finger | the same: a rest pose whose open jaws point down at the table around the object. A gripper merely left open is not enough, as the next row says |
| `lerobot mujoco: the scene lays block between the jaws, and as the arm starts, closing the gripper stops its moving finger ... mm clear of block, which it never touches on the way. ...` | the fixed finger stands at the object's side, but closing the gripper swings the moving finger past it, over its top from a hand tilted at the wrist: the connect closes the gripper on a copy of the physics as it lays the object out, and the moving finger never touched it | the same: a rest pose whose open jaws point down at the table around the object, or lay the object on the table instead |
| `QUACKD_LEROBOT_SIM_ASSETS=... has no so101_new_calib_camera.xml.` | the variable points somewhere without the model | point it at the `Simulation/SO101` directory of an SO-ARM100 checkout, or unset it to let quackd fetch the pinned model |

## Which of this arm's verbs are a choice

Only relevant with the optional `--decision-llm` ([decision-llms.md](../decision-llms.md)), and
off unless you name one.
The stepper decides what it may answer from each tool's own JSON schema, and on this arm the
split falls like this:

| | Tools | The calls it can author |
|---|---|---|
| **A choice** | `report_state`, `stop`, `place`, `gripper`, `observe` (when a camera is configured) | `report_state`, `stop`, `place`, `gripper(open=true)`, `gripper(open=false)`, `observe` |
| **A number** | `move_joints`, `pick` | none, ever |
| **A sentence** | `assess_task`, `declare_success`, `declare_failure`, `remember` | none, ever |
| **A segment** | `manipulate`, when a task file lists its instructions | one per instruction, offered and compared and never taken |

`gripper` is a choice because its only parameter is a boolean. `move_joints` is not, for two
reasons that hold independently. Its `positions` is a required object, which is enough on its
own. And the joint names are nowhere in the schema: they are enforced by a `field_validator`
against `JOINTS`, so there is nothing for a decision LLM to enumerate even in principle, and no
version of this could be talked into offering one.

`pick` is out on both counts, being a free string and confirm gated. `manipulate` is a free
string too, unless a `duck: 3` task file lists its instructions, which makes it one choice per
instruction. The stepper is then shown each of them and never takes one, in either mode: under
`--yes` nobody is asked at a confirm gate, so a stepper that cleared its floor would start the
arm's policy with no person and no model involved. Its answer is recorded beside the model's
(`gate: shadow_only`), which is the agreement rate promoting it would need
([ADR-0048](../adr/0048-policies-are-the-arms-executor.md)). The step cap, the range refusal and
the hot-servo precondition apply to a stepper-authored call exactly as they apply to a model's,
because both go through the same executor.

[`ducks/arm-grip-check.duck`](../../ducks/arm-grip-check.duck) is the task built out of the
first row alone, and it is the worked example on that page.

## Safety

Each of these exists because upstream could not answer a question quackd has to ask; the
reasoning is in [ADR-0036](../adr/0036-what-the-arm-does-not-say.md). What stops each body in
quackd, side by side, is [safety.md](../safety.md).

- **The heartbeat reads the arm.** `is_connected` is the serial port's open flag and stays
  `True` with the cable pulled, so the heartbeat is a round trip to the motors, and a dead arm
  ends the run. It reads the arm itself while a `pick` is running too, queued behind the
  loop's read. The loop's reads would do for a round trip, but each went out before the beat
  asked, and an arm that died as one came back would pass the beat and end the run a whole
  period later. So a beat costs a running pick one read of the arm.
- **Torque and temperature are measured.** `get_observation()` reads positions only, so
  `Torque_Enable` and `Present_Temperature` are read off the bus. A body joint at or above
  60 °C refuses `move_joints` and `pick`; the servo's own cut-off is 70 °C.
- **A goal outside the calibrated range is refused, on four joints of the five.** LeRobot
  does not clamp a degrees goal and the servo does, silently, to the travel calibration wrote
  into it (`up.POSITION_LIMITS_CLAMP_GOALS`), so a goal let through would be one the arm quietly
  stops short of. quackd computes each joint's travel from the calibration file and refuses
  instead. `wrist_roll` is the exception, and it is upstream's: its
  calibration deliberately does not sweep that joint, printing *move all joints except
  'wrist_roll'* and recording a full encoder turn for it instead
  (`up.WRIST_ROLL_IS_A_FULL_TURN`). Its travel therefore comes out as -180..180, and a refusal
  that cannot be narrower than the whole turn cannot catch anything. Treat `wrist_roll` as
  unguarded and give it small goals.
- **One action moves a joint at most one step.** `max_relative_target` is unset upstream;
  quackd sets it to 5 degrees, re-sent at 10 Hz, so 50 degrees a second at most. That is a
  ceiling and not a pace: `move_joints` walks its goal across the `duration_s` it is given, and
  only a time too short for the distance runs at the cap.
- **No deadman.** Nothing in LeRobot's `Robot` stops an arm when the client goes quiet: read
  from the class, not assumed. quackd's `stop` re-sends the present position as the goal and
  never calls `disable_torque()`, the same principle as never sending `robot.relax` to a
  Microduck. A joint that reads past its travel gets no goal from a stop at all, because the
  servo would clamp "stay here" to its limit and drive there
  ([A pose past the travel](#a-pose-past-the-travel)).
- **`stop` leaves the gripper's goal alone.** It sends the five body joints and omits the
  gripper key, so a stop never opens a hand that is squeezing something, and every failed
  verb ends in a stop.
- **The native limit is the gripper and only the gripper.** `configure()` caps the gripper's
  torque and current inside a check for that motor's name; the five body joints get nothing,
  so `extras.torque_limit_scope` says `gripper_only`. The gripper itself is not heat-gated,
  because opening it is how you put down what it is holding.
- **A wedged call is not a finished call.** A call that blows its deadline leaves a thread on
  a half-duplex bus, so the transport refuses every later call until that thread comes back
  rather than starting a second one. The arm holds its goal meanwhile.
- **Torque is released only where the arm is known to be at its recorded rest pose, unless a
  person holding it asks.** `disconnect()` disables it where LeRobot's config asks, which is
  LeRobot's default, and quackd asks for it over an arm at rest, because an arm at rest should
  be limp: that is what "at rest" means. So before the disconnect quackd reads the joints one
  last time, and where they are not the pose you recorded, or as near it as the calibrated
  travel lets the servo go, it asks for torque to be kept instead and leaves the arm holding
  itself up, with one line saying so and naming the ways out:

  ```
  the arm is not at its rest pose (...), so torque was left on and it will not fall as it
  stands: hold it first, because connecting takes torque off every motor for a moment, then
  run quackd robot release NAME, or quackd doctor --robot NAME to park it, or cut its power
  ```

  The hold comes before every way out, because both commands begin by connecting, and the
  connect is the next bullet. At a terminal, a run whose rest move missed asks first: hold the
  arm and press Enter within 60 seconds, and torque comes off where it stands. The line above is
  what follows when nobody presses Enter, and what `doctor`, a dry run, an MCP session or a
  flock member says without asking. With no rest pose recorded there is nothing to check
  against, nothing changes, and the arm goes limp at the end of every clean session exactly as
  it did in 0.9. See [The rest pose](#the-rest-pose), and for the person holding an arm left up
  this way, [Releasing it where it stands](#releasing-it-where-it-stands).

  An exit that never reaches that read, a second Ctrl-C during the rest move or a crash, leaves
  the arm holding too. LeRobot can still disconnect an arm nobody closed as the process lets go
  of it, and quackd builds the arm asking that disconnect to keep torque, so the release is
  asked for only by quackd's own close, or by a connect it refused because the arm is not
  calibrated, has no calibration file or has no motors bus. In 0.14 and before the arm was built
  asking that disconnect for the release, so it could fall wherever it stood. Hold the arm and
  run `quackd robot release NAME`, or cut its power. A connect that fails any other way once the
  arm is energised keeps its torque as well, and says so
  ([When it will not work](#when-it-will-not-work)).
- **Connecting still drops torque briefly, and that has not changed.** `configure()` runs
  inside `torque_disabled()`, so the arm is limp for the moment between the port opening and
  the configuration landing, whatever any rest pose says. Support the arm when a session
  starts, including at the start of a `doctor` probe, which says so before it connects to a
  body that is handed to people:

  ```
  ⚠ connecting takes torque off every motor for a moment, because LeRobot configures them with it
  off: support the arm until doctor has finished with it
  ```
- **A packet lost while connecting is tried again, and said.** Those torque writes are a
  `Torque_Enable` and a `Lock` per motor, off on every motor and back on one at a time, each
  tried once (`up.CONFIGURE_TORQUE_WRITES_ONCE`), so one status packet the bus drops, or that
  comes back garbled, fails the whole connect and leaves the port open behind it. On 2026-09-23
  that ended three of 26 runs on an SO-101 before they began, on a different motor each time,
  and the next connect went through every time. quackd now closes the port through the bus
  without writing to any motor (`up.BUS_DISCONNECT` with `disable_torque` False: the follower's
  own `disconnect()` would first switch torque off on every motor again, on the bus that has
  just lost a packet), waits half a second and connects again, up to three attempts in all. Each
  retry is a WARNING line while it happens, a note in the run's transcript and an advice line in
  `doctor`, and it names the joint through the bus's own motor table (`up.BUS_MOTORS`) rather
  than by an assumed order:

  ```
  connect attempt 1 of 3 failed on <joint> (id <N>): Failed to write 'Lock' on id_=<N> with
  '1' after 1 tries. [TxRxResult] There is no status packet! The port was closed without a
  write to any motor, and connect runs again
  ```

  A camera opened before the arm stays open across the attempts. A connect that blew its 30
  second deadline is never tried again, because its thread is still on the bus and a second
  talker there is how packets get lost. Every attempt runs `configure()` again, so the limp
  moment above happens once per attempt. When the last attempt fails too, the port is closed
  the same way and the refusal says the arm may be left half energised
  ([When it will not work](#when-it-will-not-work)), if any attempt can have written torque:
  one that failed on a write or inside `configure()`, one that failed somewhere nothing can
  place, or a connect that ends on a timeout. A servo that does not answer its ping is refused
  by LeRobot's handshake, before `configure()` has written anything (`up.BUS_HANDSHAKE`), so
  that refusal names the joint (`up.HANDSHAKE_NAMES_THE_ID`) and says nothing about torque. So
  does a read lost in the calibration check LeRobot's connect makes between the handshake and
  `configure()`, whatever `calibrate` says (so_follower.py line 99, `up.BUS_IS_CALIBRATED`),
  which reads each motor's limits and writes nothing. A handshake that found none of the arm's
  motors, which is how a servo supply that is switched off looks, names no joint at all, since
  one joint's cable would be a guess, and sends you to the arm's cables and power.

  A stop asked for while a connect is failing ends it: a Ctrl-C during a `quackd run`, whose
  first press sets the run's abort flag, is looked for once an attempt has failed and throughout
  the pause before the next, and the connect is refused on the spot with no further attempt, and
  so no further torque switched off and on
  (`connect stopped after attempt <k> of 3, because a stop was asked for`). A stop asked for
  during an attempt that then connects is answered by the run as any other stop is, after the
  rest move that opens it.

  A serial error in the middle of a packet, a USB glitch rather than a lost reply, leaves the
  servo SDK's busy flag raised, and reopening the port does not lower it, so the next attempt
  would be answered "port in use" before a byte went out and refused as every motor missing.
  quackd lowers the flag when it closes the port between attempts, in the same call and once
  the port has shut, so never under another call's packet. Upstream's own disconnect lowers it
  too, and lowering it writes nothing to a motor (`up.BUS_DISCONNECT`).
- **`pick` and `manipulate` are confirm-gated**: a learned policy moves the whole arm. Its
  actions are capped at the verbs' own speed, per second rather than per send, so a policy
  faster than the verbs' tick takes smaller steps rather than moving faster, and a goal past the
  travel is clipped and counted rather than refused
  ([What `pick` needs](#what-pick-needs-and-what-it-does-not-have)). A joint that reads past
  its travel is left out of every goal a policy sends, because the servo would take any goal
  for it as the end of the travel and drive there at its own speed, which no cap slows, and
  quackd cannot halt that rise once a move has started it. Under `--yes`, or with a pipe or file
  on stdin, the gate asks nobody, so a segment the pilot calls goes ahead without anybody
  being asked, and over MCP both verbs need `--yes`. The policy itself runs in a server of its
  own, and no quackd command loads a checkpoint in the process that holds the bus
  ([policies.md](../policies.md)).

## The rest pose

A LeRobot arm goes limp when a session ends, because `disconnect()` disables torque by its own
default, and a clean close with no rest pose recorded asks for exactly that. On the bench on
2026-09-15 that meant the arm fell at the end of every single run. Runs also started from wherever the previous one had left the
arm, so the pose a model was improvising from was different every time.

A rest pose fixes both. You fold the arm by hand, tell quackd where that is, and quackd drives
it there at both ends of every run.

> [!IMPORTANT]
> **The first arm to run a rest pose could not reach it.** On 2026-09-23 the SO-101 of the 15th
> ran with a pose recorded, and that pose had `shoulder_lift` folded about 20 degrees past the
> floor of the travel its calibration recorded, because the calibration never saw the shoulder
> folded all the way back. LeRobot's calibration writes that travel into each servo as its
> position limits, and the servo clamps every goal to them. So the rest move drove the shoulder
> to the limit, stalled there and called the arm lost: runs aborted before their first model
> call, every run that got to its end kept torque on and finished at the power switch, and the
> `stop` at the end of a run hauled a folded shoulder up out of its fold. What quackd does with
> a pose past the travel now is [A pose past the travel](#a-pose-past-the-travel), below, and
> the whole account is [ADR-0045](../adr/0045-a-rest-pose-the-calibration-cannot-reach.md).
> That answer has run against a fake arm that clamps the way the servo does and against
> `lerobot:mock`, and not yet on the arm, so read
> [section 07 of the first run](../lerobot-first-run.md#07-record-the-rest-pose) with a hand
> near the power switch and say what happened.

### Recording it

```
quackd robot rest-pose NAME [--clear] [--yes] [--address ADDR] [--registry-dir DIR] [--json]
```

**Calibrate with the arm folded, before you record.** A servo will not be driven past the
travel its calibration recorded, so the fold you record should be inside that travel. When
`lerobot-calibrate` asks you to move every joint through its whole range, take each one all
the way into the fold you mean to rest the arm in. Where the fold still lies past the travel,
`rest-pose` says so as a warning before it asks, in the same sentence a run will use, and
records the pose anyway: it is where the arm rests, and a run parks as near it as the servo
goes ([A pose past the travel](#a-pose-past-the-travel)). A new calibration also moves the zero
of any joint whose travel it records differently, because a joint's zero in degrees is the
middle of its recorded travel. So a pose recorded before it names a different shape after it:
record the pose again, and read any task or remembered note that names an angle as meaning a
different pose too.

Then fold the arm, with nothing connected, so the pose you record is one it can hold with
torque off, and run the command: it connects, reads every joint, prints them, asks, and keeps
the pose in `~/.quackd/robots.json` beside the address and the cameras. It opens no camera,
because reading joints needs none, and it drives the arm nowhere, because the point is to let
go of it at the pose you are choosing now rather than the one you are replacing.

```
$ quackd robot rest-pose arm-01 --yes     # without --yes it prints the joints and asks
arm-01 (lerobot:mock) is at
shoulder_pan   0.0
shoulder_lift  -90.0
elbow_flex     90.0
wrist_flex     0.0
wrist_roll     0.0
gripper        100.0
✓ recorded arm-01's rest pose (6 joints)
  quackd run <duck> --robot arm-01 starts from it and returns to it before letting go
```

Those numbers are the **mock** arm's, which is where that transcript was captured. A real
SO-101 folded on a desk reports its own, and they will not look like these.

`quackd robot show NAME` prints the pose back in a `rest pose` row, and `--json` puts it under
`rest_pose`. `--clear` forgets it:

```
$ quackd robot rest-pose arm-01 --clear
✓ cleared arm-01's rest pose
  a run now leaves the arm where it stands, and torque drops there
```

A rest pose belongs to a **registered** robot, because it is read off the arm and kept under
its name. There is no `--rest-pose` flag on `quackd run`.

### What a run does with it

| When | What happens |
|---|---|
| the start of every run | the arm is driven to the pose before the pilot gets control, so what the model improvises from is the same arm every time. A run that cannot get there aborts **before the first LLM call** |
| the end of every run | between the `stop` and the disconnect, which is the only window in which putting the arm down changes whether it falls. On every exit path there is: success, failure, infeasible, a spent budget, an abort, an error and Ctrl-C. Where that rest move misses, a run at a terminal offers to release the arm into your hands on Enter ([Releasing it where it stands](#releasing-it-where-it-stands)) |
| a `--by-hand` run | the rest move still happens first, and then the arm is released **at** that pose for somebody to place, at the edge of the travel for a joint recorded past it. The pose is what makes the release safe rather than something the flag skips, and the run ends back at it unless quackd refuses to take hold of what you left. It refuses while any joint reads past its travel, a fold nobody lifted the arm out of included, so lift every joint inside its travel before you press Enter. After a refused take-hold nothing moves the arm again, and the run ends with it where it is rather than folding it: in your hands, or still at its rest pose if you never lifted it ([Placing it by hand](#placing-it-by-hand)) |
| a dry run | nothing. `--dry-run` never moves the arm, and that includes the rest move |
| an MCP session | the same at both ends, and the session refuses to start if it cannot get there |
| a pose past the travel | the arm is driven as near the pose as its servos go, which is the edge of the travel, and that counts as getting there. Torque is released at the edge, and the run says once which joint is free to settle the rest of the way ([A pose past the travel](#a-pose-past-the-travel)) |

Both ends are narrated, so the transcript says what happened rather than leaving you to infer
it from a joint reading:

```
·  note    moving to the rest pose
·  note    already at the rest pose
```

### A pose past the travel

A servo on this arm will not be driven outside the travel its calibration recorded. LeRobot's
calibration writes each joint's travel into its servo as two position limits, and the servo
clamps every goal it is written to them (`up.POSITION_LIMITS_CLAMP_GOALS`). A reading is not
clamped: with torque off a joint goes wherever a hand or its own weight puts it, past either
end. So a pose recorded off a hand-folded arm can lie past the travel, and a goal of that pose
is one the arm drives to the limit, stops at, and never reaches. That is the bench of
2026-09-23 in the box above.

quackd now plans for it, on any joint, at either end of its travel, from the numbers this
arm's own calibration gives it at connect:

- **The rest move drives to the reachable pose.** Each body joint of the recorded pose is
  clipped into its travel. The pose in `robots.json` stays exactly as you recorded it, because
  the clip is a fact about this calibration and not about your fold.
- **A clipped joint is at rest anywhere from 5 degrees short of the edge of its travel out
  past it**, on the side its fold lies. Nothing quackd sends can drive a joint past its limit,
  so a joint that reads well past it was put there with torque off: it is folded, not lost.
  Every other joint keeps the ordinary rule of within 5 degrees of its recorded angle.
- **So the arm parks at the edge, which counts as arriving, and torque is released there.** The
  joint is then free to settle the rest of the way toward its fold. An arm already folded past
  the edge is `already` at rest, and the rest move sends that joint nothing, because the only
  goal it could send is the limit, and that would haul the joint up out of its fold.
- **A stop writes no goal for a joint that reads past its travel**, and the take-hold of a
  [hand-placed start](#placing-it-by-hand) does not switch torque on under one at all. "Stay
  where you are" written to that joint arrives as "go to the limit", and the servo does it at
  full speed: on the bench, the stop at the end of a run hauled a folded shoulder up out of its
  fold this way. The joint keeps whatever goal its servo already holds instead, and the stop's
  summary names each joint it wrote no goal for and says why, so the pilot and the record can
  tell a stop that held all five from one that held some. If every body joint reads past its
  travel, nothing is sent at all. What the skip does is avoid *starting* a rise, and that is
  all: any goal quackd writes to a joint while it reads past its travel is the limit to the
  servo, so a joint a move had already begun lifting out of its fold keeps rising to that limit
  whatever a stop writes or leaves out. Only the power switch stops that stretch
  ([safety.md](../safety.md)).
- **A move out of the fold starts at the edge.** `move_joints` on a joint that reads past its
  travel paces its ramp from the edge of the travel, not from the reading: any goal between the
  two is, to the servo, the edge, so the joint rises to it at the servo's own speed first,
  whatever quackd sends. Only the rest of the move is paced by `duration_s`
  ([The manifest](#the-manifest)).
- **A joint that stops short *inside* its travel is still a miss.** A hand, the desk or a
  tripped servo in the way keeps torque on, as it did in 0.13.0, and prints the line in
  [The torque rule](#the-torque-rule).

The run says what happened, once, right after its first `at the rest pose`, in this arm's own
numbers. Captured on `lerobot:mock`, whose `shoulder_lift` travels -100 to 100, registered as
`arm-01` with a rest pose that has `shoulder_lift` at -118:

```
·  note    moving to the rest pose
·  note    at the rest pose
·  note    shoulder_lift is recorded at -118 in the rest pose and this calibration lets its servo be driven to -100 and no further, so it parks there and is let go of there, free to settle the rest of the way on its own. Calibrate again with the arm folded (lerobot-calibrate) and record the pose again (quackd robot rest-pose arm-01) to make the fold reachable
```

The end of that run's transcript reads `already at the rest pose`, and says nothing more,
because the note is about the pose rather than about either move. The same sentence is what
`doctor` prints as advice under its table, with the `rest pose` row still green
([doctor](#doctor-and-robot-list---probe)), what an MCP session logs when it parks at connect,
and what `quackd robot rest-pose` warns before it asks. It names only a joint clipped by more
than 5 degrees, since a smaller clip is inside the tolerance any reached pose may miss by. The
run's first record names every clipped joint, whatever the amount, in the manifest:

```
"rest_pose_clipped": {"shoulder_lift": {"recorded": -118.0, "reachable": -100.0}}
```

**The pilot is told why a joint can read past its travel.** The travel line of its system prompt
now ends *A joint can read past its travel when it was folded or placed there with torque off,
which is where a rest pose usually is; goals are still limited to the travel.* And
`report_state` adds a clause for each joint that reads more than 2 degrees past its travel, in
the form
`<joint> reads <angle>, past the <limit> its servo can be driven to; goals are still limited to its travel`.
On the bench, a pilot handed a shoulder reading past the end of its travel line, with nothing to
explain it, refused to move the arm at all.

**The fix is a calibration that saw the fold.** Calibrate again with every joint taken all the
way into the fold during the sweep, then record the pose again, for the reasons in
[Recording it](#recording-it). The note goes away once no joint of the pose lies more than 5
degrees past its travel.

> [!NOTE]
> Parking at the edge and letting go there has run against a fake arm that clamps goals the way
> the servo does and against `lerobot:mock`, and not yet on the arm. Two things only a bench
> can say: whether a joint let go at the edge settles onto its fold, and gently, and whether one
> folded past its *ceiling* settles at all, since its weight need not pull it toward the fold.
> The note says the joint is free to settle, not that it will. And the take-hold leaves torque
> off under a joint placed past its travel rather than trust `up.TORQUE_ENABLE_HOLDS_PRESENT`
> below with it ([Placing it by hand](#placing-it-by-hand), step 4).

### Placing it by hand

A rest pose is a fold, and a fold is the wrong place to begin some tasks from. A drawing run
starts with a pencil in the gripper and its tip near the paper, and nothing the arm can be
driven to from a folded start puts it there: the pencil has to be handed to it. `quackd run
--by-hand` gives you the arm for exactly that moment and takes it back when you are done:

```bash
quackd run --goal "draw the circle in the picture" --robot arm-01 --by-hand \
  --image circle.png --llm anthropic --max-steps 12
```

The order below is the whole of the feature, and none of it is a step you can skip:

1. **The arm goes to its recorded rest pose first**, the same move that starts every other run.
   A run that cannot get there aborts here, before any torque is touched, because an arm that
   did not reach the pose is precisely an arm that must not be released at it.
2. **Torque comes off, at that pose and nowhere else.** `let_go()` re-reads the joints and
   refuses anywhere but the recorded pose, judged exactly as the close judges it, so a joint
   recorded past its travel counts at the edge of it or beyond
   ([A pose past the travel](#a-pose-past-the-travel)): an arm held up by torque alone falls the
   moment torque goes, and the person who asked for this still has their hands nowhere near it.
   It also refuses a pose that names no joint this arm drives, and an arm that still reports
   torque on after the call, which is a release that did not take rather than one to walk away
   from. LeRobot tries each torque write once unless told otherwise, and one lost packet there
   would release the motors before it and not the ones after, so the release asks for the five
   retries LeRobot's own `disconnect()` gives the same writes, and so does the take-hold in
   step 4.
3. **You are told the arm is yours, and quackd waits for Enter.** There is no timeout on this
   wait. The arm is limp at a pose it holds by its own shape, so nothing is being spent by
   waiting, and somebody who has gone to find a pencil should come back to a run that is still
   there.
4. **quackd takes hold of whatever you left.** The present position is written as the goal
   *before* torque comes on, written again after, and then the arm is read back. A joint that
   moved more than the same **5 degrees** every other goal on this page is judged by is a
   refusal and the run ends, because a run that started from a pose nobody chose is a run whose
   first observation is a lie. A joint you placed past its calibrated travel is refused before
   any of that: nothing is written, torque stays off, and the run ends with the arm still in
   your hands. Nothing quackd can do would keep that joint where you put it. A goal written
   where it is lies past the travel and is pulled to the end of it, so torque would drive the
   joint there under your hand, and with no goal written its servo keeps the last one it had,
   the rest move's, which can be the far end of the travel from where you put it. The refusal
   names each such joint, where it reads and its travel, in this arm's numbers, and says the arm
   is taken hold of only with it inside. A fold that lies past the travel
   ([A pose past the travel](#a-pose-past-the-travel)) counts, so lift every joint out of it
   before you press Enter. Press Enter with the arm still lying in that fold and you are told it
   is still limp at its rest pose, with the joint to lift inside its travel, rather than that it
   is in your hands. A take-hold refused after torque was asked for, because the torque
   register did not answer or the call failed on the way, is a different arm: it may be holding
   itself up, all of it or part of it, and you are told quackd could not confirm whether it has
   torque, to hold it as though it may move or drop, and to cut its power to be sure. Where the
   read after the torque write found motors on, some of them or every one, you are told what
   it found instead: which joints hold, that any joint it does not name is limp, and to keep
   hold of it and cut its power.
5. **The pilot runs from those angles.** The step cap, the range refusal, the heat gate and the
   budgets are all the ones any other run gets. The minutes clock restarts the moment the arm
   is holding your pose, so the time you spent looking for a pencil is not taken out of the
   model's: a person's time and a pilot's budget are not the same clock.
6. **At the end you are asked before the gripper opens.** The run's own `stop` is holding the
   arm where it finished; the question comes next and the opening after it, because an arm
   folding to its rest pose with a pencil still in the jaws drives that pencil into the bench,
   and the person who put it there is the one who should take it out. That wait is bounded at
   **120 seconds**, since a run has to end even when the room is empty.
7. **Then the fold.** Enter sends `gripper(open=true)` through the logged transport, so it is
   in the record like every other intent, and then the rest move folds the arm and torque drops
   at the fold exactly as it would have without the flag.

What the person at the arm is told, in order, captured from a `--by-hand` run on `lerobot:mock`
in which the arm was placed at `shoulder_lift` -20, `elbow_flex` 40, `wrist_flex` 15 with the
gripper squeezed to 35:

```
the arm is yours: torque is off at its rest pose, so lift it, put whatever it needs in
the gripper, close the gripper on that, hold it where you want the run to start, and
press Enter
holding the pose you set, you can let go. It is at elbow_flex 40, gripper 35,
shoulder_lift -20, shoulder_pan 0, wrist_flex 15, wrist_roll 0
the run is over and the arm is holding where it ended. Take hold of whatever is in the
gripper and press Enter, and the gripper opens before the arm folds up. Leave it and
the arm folds up with the gripper shut
```

The middle line reads the arm back rather than repeating what it was told to hold, which is how
you find out that the wrist sagged two degrees as it took the weight. The same run in the
transcript, where every stage is a `hand` event, with the model's side of each turn, the
verdict, the declaration and the `asked` record of each question left out:

```
·  note    moving to the rest pose
·  note    already at the rest pose
·  hand    released: torque is off at the rest pose
·  hand    held: holding the pose you set (elbow_flex 40, gripper 35, shoulder_lift -20, shoulder_pan 0, wrist_flex 15, wrist_roll 0)
▶  verb    report_state()
✓  result  report_state ok: shoulder_pan 0, shoulder_lift -20, elbow_flex 40, wrist_flex 15, wrist_roll 0, gripper 35; torque on; hottest shoulder_pan 30°C; holding nothing (0.0 s, 0 intents)
→  send    stop
→  send    gripper(open=true)
·  hand    unloaded: opening the gripper
·  note    moving to the rest pose
·  note    at the rest pose
```

**The pilot is told where it is starting from**, in a `## Where this run starts` section of the
system prompt that only a hand-placed run has. A model that assumed the fold would improvise
from a shape the arm is not in, so it is told to read `report_state` and work from the angles
rather than from any remembered geometry. That section also says what `holding nothing` above
means, in whichever of the two ways applies: a task whose allowlist has the gripper verb is
told to close on the object before leaning on it, and a task like `lerobot-lookout`, which
cannot work the gripper at all, is told that whatever is between the jaws is held at the
squeeze the person left and cannot be tightened.

**Ctrl-C in the first wait** ends the run with the arm limp in your hands, and the teardown it
goes into is built for exactly that arm. Every teardown starts with a `stop`, and a `stop` on an
arm somebody is holding takes hold of it first: the arm is re-energised where your hand has it,
and only then does the rest move fold it. A `stop` that sent a goal to a limp servo would stop
nothing, and the fold after it would be a fold of an arm that is not listening. Not while a
joint reads outside its travel: the take-hold refuses there as it does in step 4, you are told
so once, naming the joint, and then nothing is written to the arm and nothing folds it. The
close says what its own read finds, the arm limp in your hands, or, where the joint outside its
travel is a fold nobody lifted the arm out of, the arm limp at its rest pose (captured on
`lerobot:mock` registered as `arm-03`, its pose's `shoulder_lift` recorded at -110, past the
mock's -100, with the capture script letting the joint settle there once torque was off, as a
fold past the travel does, and pressing Ctrl-C in the wait, with the invitation, the kill
switch's own line and the record of the question left out):

```
·  hand    released: torque is off at the rest pose
→  send    stop
·  hand    hold refused: shoulder_lift reads -110.0, outside its calibrated travel of -100.0..100.0, and quackd takes hold of the arm only once shoulder_lift is lifted inside its travel
·  note    the arm did not take hold: shoulder_lift reads -110.0, outside its calibrated travel of -100.0..100.0, and quackd takes hold of the arm only once shoulder_lift is lifted inside its travel
quackd did not take hold of the arm when the run stopped and the arm is still limp at its rest
pose: shoulder_lift reads -110.0, outside its calibrated travel of -100.0..100.0, and quackd takes
hold of the arm only once shoulder_lift is lifted inside its travel
·  note    already at the rest pose
·  note    the arm is limp at its rest pose, where it was let go of for you to place and where it still reads: it rests there with no torque, as it does at the end of every run
```

With the arm lifted and a joint placed past its travel, the line said to you at the stop gives
the joint, its reading and its travel in the same words as the refusal at Enter below, after
`quackd did not take hold of the arm when the run stopped and the arm is still in your hands:`,
and the close is the one for an arm limp in your hands.

A wait that ends any other way lands in the same teardown and says so rather than blaming a
key nobody touched. There is no clock on the first wait, so that ending means the keyboard
itself went away: the terminal was closed, or the input it was reading finished. quackd
notices, because a wait for a keystroke that nothing can deliver has to end rather than hold an
arm limp for ever (captured on `lerobot:mock`, where nobody had moved the arm out of the fold
either, so the rest move had nothing to do, with the invitation and the record of the question
left out):

```
·  hand    released: torque is off at the rest pose
→  send    stop
·  note    moving to the rest pose
·  note    already at the rest pose
┌─ ✗ ABORTED ─────────────────────────────────────────────────────────────────────────┐
│ nobody placed the arm: it was released at its rest pose for somebody to put it      │
│ somewhere, and nothing was pressed                                                  │
│ steps 0 · llm calls 0 · tokens 0+0 · time 0.1 s (model 0.0 s) · cost $0             │
│ run dir  runs\20260924-163419-lerobot-lookout                                       │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

**Ctrl-C in the second wait means "skip this and finish"**, not "abandon the arm". The gripper
stays shut, the stage is recorded as skipped, and the rest of the teardown runs anyway, which
is the difference between a run that ends with the arm folded and a record written and one that
leaves an energised arm and no transcript. A third press lands somewhere without that guard and
quits at once:

```
·  hand    skipped: interrupted while waiting
·  note    the gripper was left as it is, and the arm still folds up
```

The first Ctrl-C of a run nobody had stopped yet, or `q`, ends this wait without raising
anything, since the wait watches for a fresh key press and not for the run being stopped, and it
is recorded in the same words. It used to be filed as nobody answering. A terminal with no keys
to read says so. Captured on `lerobot:mock` with the key reader stood in for, Enter at the first
wait and the kill switch at this one:

```
·  hand    skipped: interrupted while waiting
·  note    the gripper was left as it is, and the arm still folds up
```

and with no keys to read:

```
·  hand    skipped: no key could be read
·  note    no key could be read, so the gripper stays shut and the arm folds up
```

Walking away and pressing nothing at all is the same ending by a different route, once the
120 seconds are up:

```
·  hand    skipped: nobody answered
·  note    nobody unloaded the gripper, so it stays shut and the arm folds up
```

**A joint placed past its travel ends the run with the arm in your hands**, as step 4 says. You
are told at once, not only in the summary, and from then on nothing touches the arm: the stop
that begins the teardown takes no second hold and writes nothing, the gripper is left for you to
empty by hand rather than asked about, the rest move writes nothing either and the run says once
that the arm is not folded, no release is offered over an arm in your hands, and the close ends
on the line for an arm in your hands. Captured on `lerobot:mock` registered as `arm-01`, with
your hand stood in for by the capture script, which set `wrist_flex` to 112, past the mock's
100, before the Enter, and with the log's copy of the second line said to you left out:

```
·  hand    hold refused: wrist_flex reads 112.0, outside its calibrated travel of -100.0..100.0, so quackd left torque off: a goal written where that joint is lies past its travel and the servo would pull it to the end of its travel, and with none written the servo may drive it to the last goal it was given, with a hand on the arm either way. quackd takes hold of the arm only with wrist_flex inside its travel
·  note    the arm did not take hold: wrist_flex reads 112.0, outside its calibrated travel of -100.0..100.0, so quackd left torque off: a goal written where that joint is lies past its travel and the servo would pull it to the end of its travel, and with none written the servo may drive it to the last goal it was given, with a hand on the arm either way. quackd takes hold of the arm only with wrist_flex inside its travel
quackd did not take hold of the arm, so the run stops here and the arm is still in your hands:
wrist_flex reads 112.0, outside its calibrated travel of -100.0..100.0, so quackd left torque off:
a goal written where that joint is lies past its travel and the servo would pull it to the end of
its travel, and with none written the servo may drive it to the last goal it was given, with a hand
on the arm either way. quackd takes hold of the arm only with wrist_flex inside its travel
→  send    stop
·  hand    skipped: the arm is still in your hands
quackd never took hold of the arm, so it does not open the gripper for you: take out whatever is in
it by hand, and keep hold of the arm
·  note    the arm is in your hands, so it is not folded
·  note    the arm is limp and in your hands (it was let go of for you to place and never taken hold of again): put it down before you let go of it, because nothing is holding it up
```

Moving the joint back inside its travel changes none of that, on the arm or on the mock. Nothing
takes hold of the arm again after a refusal until it is released again, because torque coming
on under your hand with nothing said is the one thing the lines above told you would not
happen. Put the arm down, and run again with every joint inside its travel.

**Enter pressed over a fold nobody lifted** is refused the same way and said as what it is. The
take-hold's own read finds the whole arm at its rest pose with every motor off, so you are told
the arm is still limp at its rest pose and which joint to lift inside its travel, not that it is
in your hands and to keep hold of it, and the close's own read says the same. Captured on
`lerobot:mock` registered as `arm-03`, as in the Ctrl-C capture above, with Enter pressed and
nothing lifted, and with the log's copies of the lines said to you left out:

```
·  hand    hold refused: shoulder_lift reads -110.0, outside its calibrated travel of -100.0..100.0, and quackd takes hold of the arm only once shoulder_lift is lifted inside its travel
·  note    the arm did not take hold: shoulder_lift reads -110.0, outside its calibrated travel of -100.0..100.0, and quackd takes hold of the arm only once shoulder_lift is lifted inside its travel
quackd did not take hold of the arm, so the run stops here and the arm is still limp at its rest
pose: shoulder_lift reads -110.0, outside its calibrated travel of -100.0..100.0, and quackd takes
hold of the arm only once shoulder_lift is lifted inside its travel
→  send    stop
·  hand    skipped: the arm is still limp at its rest pose
quackd never took hold of the arm, which is still limp at its rest pose, so it does not open the
gripper for you: take out whatever is in it by hand. quackd takes hold of the arm only once
shoulder_lift is lifted inside its travel
·  note    already at the rest pose
·  note    the arm is limp at its rest pose, where it was let go of for you to place and where it still reads: it rests there with no torque, as it does at the end of every run
```

**A take-hold refused after torque was asked for** ends the same way and is said differently,
because that arm may be holding itself up: the torque register did not answer the read after the
torque write, or the call raised with the write on the wire. Nothing folds it and nothing is
written to it, and the close says what a read found, or that none did. A read after the torque
write that found motors on, some of them or every one, is said by what it found: that those
joints hold and any joint it does not name is limp, to keep hold of the arm, and to cut its
power. A read counts only when the bus carried it after the torque write, so a heartbeat read
that got the bus just before the write never answers for it. Captured on `lerobot:mock`
registered as `arm-01`, placed inside its travel, with the capture script standing in for a
torque register that does not answer, and with the log's copy of the second line said to you
left out:

```
·  hand    hold refused: the arm did not say whether torque came back on (RuntimeError: Incorrect status packet!), and a hold nothing confirmed is not a hold
·  note    the arm did not take hold: the arm did not say whether torque came back on (RuntimeError: Incorrect status packet!), and a hold nothing confirmed is not a hold
quackd could not confirm whether the arm has torque (the arm did not say whether torque came back
on (RuntimeError: Incorrect status packet!), and a hold nothing confirmed is not a hold), so the
run stops here: keep hold of the arm as though it may move or drop, and cut its power to be sure
→  send    stop
·  hand    skipped: the arm is still in your hands
quackd could not confirm whether the arm has torque, so it does not open the gripper or fold the
arm: keep hold of it as though it may move or drop, and cut its power before you take out whatever
is in the gripper
·  note    the arm is in your hands, so it is not folded
·  note    the arm is in your hands, and quackd asked its motors for torque to take hold of it and nothing read back what they did, so it may hold itself up or be limp, all of it or part of it: keep hold of it as though it may move or drop, and cut its power to be sure
```

> [!WARNING]
> There is one window in which you are holding an arm that nothing is holding up, and it runs
> from the release to the moment quackd takes hold again. A run that ends inside it goes into
> a teardown that tries to pick the arm back up once, and only where no take-hold has been
> refused since the release. Where one has, nothing touches the arm again, and the close says
> what it reads in its own words rather than printing the line about torque being left on,
> which would tell somebody with a limp arm in their hand that it is holding itself up.
>
> ```
> the arm is limp and in your hands (...): put it down before you let go of it, because
> nothing is holding it up
> ```
>
> Put it back in the fold before you let go, then run again.

**The five refusals**, all five of them before anything is released, before anything
connects and before a run directory exists:

| What you see | Why |
|---|---|
| `--by-hand is one person placing one arm, and this run has several robots` | one pair of hands and one terminal. Dropping `--flock` and `--robots` is the fix, and the flag is refused rather than quietly applied to the first body |
| `--by-hand and --dry-run ask for opposite things: one takes torque off the arm, the other moves nothing` | a dry run moves nothing at either end, and taking torque off an arm is the one thing here that is not a command to the robot but a change to it. Rehearse with `--dry-run`, then run it again with `--by-hand` |
| `--by-hand waits for you to press Enter, and there is no terminal to ask on` | the wait reads a real keystroke. With nobody to ask, the release would happen and nothing would ever pick the arm back up |
| `microduck:mock is not a body a person places by hand: only the LeRobot arm is` | a body declares `supports_hand_off`, and six of the seven do not. `quackd list-adapters` |
| `--by-hand releases the arm at its recorded rest pose, and this arm has none recorded` | the release refuses anywhere but the recorded pose, so an arm without one could never be handed over at all. Said here rather than after it has connected: `quackd robot rest-pose NAME` |

> [!NOTE]
> The hand-off is exercised against `lerobot:mock` and in the test suite, and not yet on a
> real arm. The step that most wants one is the take-hold: whether a servo re-energised under
> the weight of an outstretched arm actually stays within 5 degrees of where a hand left it is
> `up.TORQUE_ENABLE_HOLDS_PRESENT` in the table below, and it stays an assumption until
> somebody stands there and watches it happen. Say what it did.

### The torque rule

Torque is released **only** where the arm is known to be at the pose you recorded, or as near
it as its calibration lets the servos go. A joint is at its recorded angle when it reads within
**5 degrees** of it, the same tolerance a `move_joints` goal is judged by, and every joint of
the pose has to be reported and within it. A joint recorded past its travel is at rest within
the same 5 degrees of the edge of that travel, or anywhere beyond the edge on the side its fold
lies, for the reasons in [A pose past the travel](#a-pose-past-the-travel). Where that does not
hold, quackd writes LeRobot's `disable_torque_on_disconnect` as False on the config instance
before the call, closes the port with every motor still holding its goal, and prints one line:

```
the arm is not at its rest pose (...), so torque was left on and it will not fall as it
stands: hold it first, because connecting takes torque off every motor for a moment, then
run quackd robot release NAME, or quackd doctor --robot NAME to park it, or cut its power
```

The parenthesis names the joints and how far short they are, and, where the rest move itself
failed, why it failed. It never names a joint that is folded past its travel, because that
joint is at rest. An arm parked at the edge of its travel and let go there prints nothing at
the close: the sentence about its fold was said once, by the rest move, and a close line is
read everywhere as torque left on.

The hold comes first because both commands connect, and connecting takes every motor's torque
off for a moment, so "it will not fall" is true of the arm as it stands and of nothing that
connects to it. Two closes say something else, because this line would be wrong in them. An arm
that did not answer the close's own read may be one whose supply you have just cut, limp in your
hands, as easily as one whose cable came out in front of live servos, so its line says quackd
cannot tell whether the arm is holding itself up, and to hold it and cut its power. And an arm
whose release you have just asked for and been refused is not sent back to that release
([Releasing it where it stands](#releasing-it-where-it-stands)).

`NAME` is the name the arm was registered under wherever quackd built it from the registry,
which is a run, an MCP session, a flock, `doctor --robot NAME` and the `robot` commands. A bare
spec such as `doctor --robot lerobot:real` builds the arm with no name, and leaves `NAME` as it
is rather than offering the calibration id as a name nobody may have registered. Captured from a
`--dry-run` on `lerobot:mock` registered as `arm-01` with its pose's `shoulder_pan` at 30, which
a dry run never drives it to:

```
·  note    the arm is not at its rest pose (shoulder_pan is at 0 with a goal of 30), so torque was left on and it will not fall as it stands: hold it first, because connecting takes torque off every motor for a moment, then run quackd robot release arm-01, or quackd doctor --robot arm-01 to park it, or cut its power
```

> [!CAUTION]
> This is a behaviour change. A probe or a dry run on an arm away from its recorded rest pose
> now leaves torque **on** where it used to drop it. The arm is holding itself up and the
> servos are drawing current until something stops them: hold it first, and then run
> `quackd robot release NAME`, or run `quackd doctor --robot NAME` and let the arm park itself,
> or cut its power. Both commands connect, and connecting drops torque for a moment. An arm with
> no rest pose recorded behaves as it always did.

**The exceptions are a person asking for it, out loud.** Everything above is about quackd's own
initiative, and on its own initiative quackd still de-energises nothing: no verb disables
torque, no model can reach it, and `stop` is a hold rather than a release. `let_go()` is the
single call in the project that takes torque off a robot, and it has two doors, both opened by
a person at a terminal and neither by anything else.

The first is [`--by-hand`](#placing-it-by-hand), and it is guarded by the rule above read from
the other side. The close keeps torque on where the arm is not at its recorded rest pose; the
release refuses where the arm is not at it. Both are the same question, *is this arm somewhere
it can be let go of*, asked of the same joint reading with the same 5 degrees of slack, and the
answer that leaves an arm holding itself up is also the answer that will not hand it to you,
because the person who asked for it has their hands nowhere near it yet.

The second is for the arm the close has just left holding itself up, and it releases that arm
wherever it stands, because the person asking is holding it:
[Releasing it where it stands](#releasing-it-where-it-stands), below, which is
`quackd robot release` and the offer a run makes at a terminal when its rest move missed.

### Releasing it where it stands

On 2026-09-23 every run that got to its end kept torque on, because its rest pose could not be
reached, and every one of them finished at the power switch: `let_go()` refused anywhere but the
rest pose, and nothing else in quackd would take torque off. An arm holding itself up against a
pose it could not reach is the right thing to leave in an empty room, and a dead end for a
person standing next to it. So there is a second door, for exactly that person:

```
quackd robot release NAME [--yes] [--address ADDR] [--registry-dir DIR]
```

It says two things before it touches anything, because both happen to an arm you should already
be holding. Connecting takes torque off every motor for a moment, since LeRobot's `configure()`
runs inside `torque_disabled()`, so a warning printed after the connect would come after the arm
had already been limp once. And the release lets the arm fall from wherever it is. Then it asks,
and only then connects, with no camera and with the registered rest pose, so a release that does
not happen closes under the rule above. It prints the joints, sends the release with no `stop`
before it (a stop picks an arm in somebody's hands back up), reads `Torque_Enable` back off
every motor, and says what it read. Captured on `lerobot:mock` registered as `arm-01`, with `y`
typed at the question:

```
⚠ connecting takes torque off every motor for a moment, because LeRobot configures them with it
off, and the release then lets the arm fall from wherever it is: hold it now, and keep hold of it
until it is down
release torque on arm-01? [y/N]: y
arm-01 (lerobot:mock) is at
shoulder_pan   0.0
shoulder_lift  -90.0
elbow_flex     90.0
wrist_flex     0.0
wrist_roll     0.0
gripper        100.0
✓ torque reads off on every joint of arm-01
⚠ the arm is limp and in your hands (torque was taken off where it stood, because you asked for
it): put it down before you let go of it, because nothing is holding it up
```

The last line is the close's own, and it is the right one to end on: nothing is holding the arm
up, and you are. Unless a motor kept its torque, and then the close's line is not that one (the
table below). `--yes` skips the question and nothing else, so hold the arm before you run it.
With no terminal and no `--yes` it refuses before anything connects
(`no terminal to ask on: pass --yes to release it`), and answering `n` connects nothing.

`torque reads off on every joint` is printed only when the register was read and every motor
said 0, and the command exits 0 only then. Everything else exits 1 and says which it was:

| What you see | What it means |
|---|---|
| `torque still reads on for <joints>: cut the power` | those motors kept their torque through the release, or every motor did and nothing was released. The ones not named are limp, and the close's line after it says which case it was: below |
| `torque was taken off and could not be read back: ...` | the release went out and the read that would confirm it failed, or the release call itself did not come back part way through its motors, and then the motors after the one it failed on kept their torque. quackd reads that silence as a release, so hold the arm as though nothing holds it, and cut its power to be sure. The close's line after it says what its own read of the arm finds, when it takes one, which it does on an arm with a rest pose, and that read answers: the arm limp in your hands, or the joints still on by name. Only when no read answers does it say that nothing read the release back |
| `nothing was released: ...` | the arm did not answer before the release, so nothing was sent. Whether it is holding itself up is not something quackd could read, so hold it |
| `lerobot:real at COM5: ...` and `keep hold of the arm` | the connect failed, and a connect that fails part way can leave some motors limp ([When it will not work](#when-it-will-not-work)) |
| `... is not a body quackd takes torque off: only the LeRobot arm is` | the name is a body that does not declare `supports_hand_off` |

After `torque still reads on`, the close's last line says what quackd then did, and never sends
you back to the command that has just failed. Some motors held and the rest let go, so the arm
is in your hands with those joints still energised, and the close keeps whatever torque there
is. Captured on `lerobot:mock` registered as `arm-01` with no rest pose recorded, its release
made to keep `elbow_flex` on, since an in-memory release otherwise always takes. At the rest
pose the second line reads
`torque is off at the rest pose except on elbow_flex, which still read on` instead:

```
✗ error: torque still reads on for elbow_flex: cut the power
  torque is off where the arm stands except on elbow_flex, which still read on
⚠ the arm is in your hands (torque was taken off where it stood, because you asked for it), but
elbow_flex still reads torque on and holds: keep hold of the arm, put it down, and cut its power to
let go of it
```

Every motor held, away from the rest pose, so nothing was released and torque is kept. The same
arm with a rest pose recorded whose `shoulder_pan` is 30, every motor kept on:

```
✗ error: torque still reads on for shoulder_pan, shoulder_lift, elbow_flex, wrist_flex, wrist_roll,
gripper: cut the power
  the arm still reports torque on, so it was not released
⚠ the arm is not at its rest pose (shoulder_pan is at 0 with a goal of 30) and the release did not
take, so torque was left on and it will not fall as it stands: hold it and cut its power
```

Every motor held at the rest pose, or on an arm with no pose recorded: the close lets go of it
there, as every such close does, which sends the same release a second time with nothing to read
it back. The line then begins
`the release did not take, and the close then took torque off at the rest pose`, and ends on
cutting the power if the arm still holds itself up.

After `could not be read back`, the close's line turns on a read of its own. On an arm with a
rest pose the close reads the arm before it decides, and when that read answers it says what the
read found: the arm limp in your hands, or the joints still on by name, with the power switch as
what lets go of them. When no read answers, or on an arm with no rest pose, which the close does
not read, nothing has read the release back, so the close does not say the arm is limp either:
the call may have stopped at one motor's write and left the motors after it holding. Its line
says that, and ends on the power switch. The mock's close takes no read of its own, so a mock
release whose read-back failed always ends this way. Captured on `lerobot:mock` registered as
`arm-02`, its read-back made to fail, since an in-memory release otherwise always reads back:

```
✗ error: torque was taken off and could not be read back: torque is off at the rest pose, and the
torque register did not answer to confirm it (the capture's stand-in for a register that did not
answer)
  hold the arm as though nothing holds it, and cut its power to be sure
⚠ the arm is in your hands (torque was taken off where it stood, because you asked for it), but
nothing read its torque back after the release went out, so it may be limp only in part: hold it as
though nothing holds it, put it down, and cut its power to be sure
```

**The offer at the end of a run.** A run with you at its terminal makes the same offer itself,
between its last rest move and the close, when that rest move missed: never on a dry run, never
over MCP and never in a flock. And never over an arm that stopped answering: a rest move that
failed because the arm went quiet is what cutting the servo supply looks like, nothing read says
that arm is holding itself up, and the release would refuse at its first read anyway. The same
goes for a rest move that left a call which never came back, a goal write or the hold a stalled
move ends with, since the bus stays wedged behind it for as long as the call is out. Enter
releases the arm through the same door. Sixty seconds with no Enter
(`AgentLoop.RELEASE_OFFER_S`), no keyboard to read, or a Ctrl-C leaves it exactly as a run
without the offer would, holding itself up with the torque line said. Captured on `lerobot:mock`
registered as `arm-01` with `shoulder_pan` at 30 in its pose and its rest move scripted to miss,
since a mock cannot miss on its own (`shoulder_pan stopped 30 deg short` is the capture script's
wording), with Enter pressed:

```
·  note    moving to the rest pose
·  note    the arm did not reach its rest pose: shoulder_pan stopped 30 deg short
the arm did not reach its rest pose (shoulder_pan stopped 30 deg short), so it
is holding itself up. Hold it and press Enter to release torque now. Leave it,
and after 60 s it stays that way
·  asked   release: the arm did not reach its rest pose (shoulder_pan stopped 30 deg short), so it is holding itself up. Hold it and press Enter to release torque now. Leave it, and after 60 s it stays that way -> yes
·  release released: torque is off where the arm stands
torque is off where the arm stands: the arm is in your hands, so put it down
before you let go of it
·  note    the arm is limp and in your hands (torque was taken off where it stood, because you asked for it): put it down before you let go of it, because nothing is holding it up
```

And left alone:

```
·  release kept: nobody pressed Enter
·  note    nobody pressed Enter, so torque stays on and the arm holds itself up
·  note    the arm is not at its rest pose (shoulder_pan is at 0 with a goal of 30), so torque was left on and it will not fall as it stands: hold it first, because connecting takes torque off every motor for a moment, then run quackd robot release arm-01, or quackd doctor --robot arm-01 to park it, or cut its power
```

The record says which of those ended the wait. A Ctrl-C at the offer, the first of the run or a
later one, reads `release kept: interrupted while waiting` rather than `nobody pressed Enter`,
and a terminal with no keys to read reads `release kept: no key could be read`. A Ctrl-C that
lands on the release itself, after Enter, is caught too, and what you are told turns on which
side of the send it landed, which the arm's backend knows. Once the release has gone out, it may
have reached some motors and not others, so the arm is taken to be limp in your hands, you are
told to hold it as though nothing holds it, the record says
`release interrupted: interrupted during the release`, and a close that nothing has read the
release back for says so, and to cut its power to be sure. Before it went out, on the read the
release begins with, nothing was sent, and that is all you are told, with torque as the rest
move left it: "the arm may be limp" would be said of a release that never happened. Whether the
arm is holding itself up is the close's line, from a read of its own, and it comes next. On the
arm the read the Ctrl-C landed on may not have come back by then, and while it is out the
close's read is refused and its line says quackd cannot tell. Captured on `lerobot:mock`
registered as `arm-02`, with the same pose and the same scripted miss as above, and the
interrupt landing there:

```
·  release kept: interrupted before the release was sent
·  note    the release was interrupted before anything was sent, so torque is as the rest move left it
the release was interrupted before anything was sent, so torque is as the rest move left it
·  note    the arm is not at its rest pose (shoulder_pan is at 0 with a goal of 30), so torque was left on and it will not fall as it stands: hold it first, because connecting takes torque off every motor for a moment, then run quackd robot release arm-02, or quackd doctor --robot arm-02 to park it, or cut its power
```

Either way the close, the summary and the rest of the teardown still happen. When the release is
refused, what you are told follows what was read: every motor reading torque on says the arm is
still holding itself up and to cut its power, and a release refused before anything was read
says quackd cannot tell whether torque is on, to keep holding the arm, and to cut its power.

The offer and what came of it are said to you directly whether or not the log is on, and the
record keeps them as a `release` event and a `prompt` row
([architecture.md](../architecture.md)).

**Why a command and a prompt, and not a verb.** Every guard on this arm is there because a model
is three seconds away from it and nobody's hands are on it. Releasing torque away from the rest
pose is the one thing that drops the arm, so it goes through the only two doors a model cannot
reach: a command a person types, and a question put to a person at the run's own terminal. It is
not a verb and is in no `allow` list, it is not an MCP tool, and `let_go` is still not on the
`RobotAdapter` protocol ([ADR-0039](../adr/0039-an-arm-placed-by-hand.md),
[ADR-0045](../adr/0045-a-rest-pose-the-calibration-cannot-reach.md)). Both say "hold it" before
anything happens, which is the whole difference from `--by-hand`, where the release comes first
and the person's hands second.

> [!WARNING]
> Neither has run on an arm. Both are exercised against `lerobot:mock` and a fake arm in the
> test suite. What an SO-101 does in the moment the release reaches it away from its fold, how
> fast a shoulder held out at an angle drops, and whether one hand is enough to catch it, are
> for the bench to say.

### What is driven, and what is not

Only the **five body joints** are ever driven. The gripper is recorded, printed and stored, and
never commanded, for the same reason `stop` omits it: LeRobot writes only the keys it is given,
so leaving the gripper out keeps whatever squeeze is already commanded, and a rest move that
re-sent the gripper would open a hand that is holding something.

The rest move goes out **clipped** into the travel, like every other goal quackd sends. Until
2026-09-23 it was the one move sent unclipped, on the belief that a servo sent the angle of a
fold outside its recorded travel would drive the arm back into that fold. The bench showed it
does not: it clamps the goal to its limit and stops there
([A pose past the travel](#a-pose-past-the-travel)). The out-of-range refusal that guards
`move_joints` is still not in the rest move's way, and would have nothing to refuse: the goal is
the recorded pose already clipped into the travel from the same calibration. Record a pose you
are willing to have the arm driven toward from wherever a run ends.

The move itself is not paced the way `move_joints` is: it re-sends the goal at 10 Hz and runs
at the 5 degree step cap, on a budget
computed from how far the arm has to travel and bounded so a teardown cannot hold a run open
for minutes. It stalls the same way a `move_joints` does, five ticks of 0.1 s in which nothing
moved, and on a stall or a spent budget it holds where it is and reports how far short it
stopped. Either of those is an arm that keeps its torque.

### doctor, and `robot list --probe`

`quackd doctor` returns a probed arm to its rest pose too, and says so in a `rest pose` row.
That matters because a `doctor` probe disconnects like anything else, and a disconnect is what
dropped torque and put the bench arm on the desk:

```
│ rest pose         │ at it already                                                               │
```

| What the row says | What it means |
|---|---|
| `at it already` | the arm was within tolerance when `doctor` looked, and nothing moved |
| `returned to it` | `doctor` drove it back, and torque dropped at the end |
| `none recorded (quackd robot rest-pose <name>)` | nothing to park to, so the probe ended the way it always did: limp |
| `not reached: ...` | the probe **fails**, the arm kept its torque, and the torque-left-on line comes out as an advisory under the table, naming the ways out: hold the arm, then run `quackd robot release NAME`, run `doctor` again to let the rest move try from where it is, or cut its power |

A pose past the travel is parked at the edge of it, and the row says `returned to it` or
`at it already` as it would for any pose, because the arm reached the pose it can be driven to
and was let go of there. What the fold means comes out as advice under the table, and the
verdict stays green. Captured on `lerobot:mock` registered with `shoulder_lift` at -118, against
the mock's travel of -100 to 100 for that joint
(`quackd doctor --robot arm-01 --address mock://arm`):

```
│ rest pose         │ returned to it                                                              │
└───────────────────┴─────────────────────────────────────────────────────────────────────────────┘
shoulder_lift is recorded at -118 in the rest pose and this calibration lets its servo be driven to -100 and no further, so it parks there and is let go of there, free to settle the rest of the way on its own. Calibrate again with the arm folded (lerobot-calibrate) and record the pose again (quackd robot rest-pose arm-01) to make the fold reachable
```

`quackd robot list --probe` does **not** move the arm. It reads, lets go, and where the arm was
not already at its pose it keeps torque and says so on the same line that says the arm
answered:

```
┌────────┬──────────────┬────────────────────────────────────────────┐
│ name   │ robot        │ reachable                                  │
├────────┼──────────────┼────────────────────────────────────────────┤
│ arm-01 │ lerobot:mock │ ✓ ok, torque left on: not at its rest pose │
└────────┴──────────────┴────────────────────────────────────────────┘
```

A real arm that stopped answering before the probe let go of it ends that cell on
`torque unknown: the arm did not answer the close` instead: quackd cannot tell whether it is
holding itself up, so it kept whatever torque there is. Hold it, and cut its power.

### Only this arm parks

Every other body refuses a rest pose rather than accepting one and ignoring it, whether it has
joints or not:

```
$ quackd robot rest-pose duck --yes
✗ error: duck (microduck:mock) has no joints, so there is no rest pose to record
  a rest pose is for an arm: quackd list-adapters

$ quackd robot rest-pose cart --yes     # xlerobot:mock, which HAS joints
✗ error: cart (xlerobot:mock) has joints, and quackd does not drive it to a rest pose yet:
only the LeRobot arm does today
```

## When it will not work

Every message below is quackd's own, quoted from the code that raises it. The arm is not
touched by anything in the first block: these all happen before or during connect.

| What you see | What it means | What to do |
|---|---|---|
| `adapter 'lerobot' needs an extra: uv pip install 'quackd[lerobot]'` | either the adapter package or LeRobot itself is missing from the environment you are running from | install the extra, and check `python --version` is 3.12 or newer, because the SDK's marker silently resolves to nothing below that |
| `doctor` shows `lerobot` green and `lerobot (feetech bus)` missing | LeRobot is installed without its `[feetech]` extra, so it imports and cannot open a serial port | `uv pip install "quackd[lerobot]"`, which asks for `lerobot[feetech]` |
| `lerobot real: --address must be the arm's serial port` | no `--address` at all | pass the port. `--address needs --robot, so quackd knows what it is connecting to` means the opposite mistake, an address with no robot to apply it to |
| `lerobot real: --address 'x' is not a serial port; it looks like COM5 on Windows or /dev/ttyACM0 elsewhere` | the address is not port-shaped | on Windows find it in Device Manager under Ports; on Linux it is usually `/dev/ttyACM0` |
| `lerobot real: connect failed 3 times: Could not connect on port ...` | the port is wrong, or something else already owns it. It is tried three times like any connect failure, since a port busy for a moment is as passing as a lost packet | LeRobot's own words come through, and they name `lerobot-find-port`, which is the way to be sure. The Feetech bus has one owner at a time, so close any teleoperation, recording or serial monitor still holding it, and on Linux check that your user can open the port (upstream's own line is `sudo chmod 666 /dev/ttyACM0`; the port's group, usually `dialout`, is the version that survives a reboot). No attempt opened the port, so nothing was written to a motor and the message says nothing about torque |
| `connect attempt 1 of 3 failed on <joint> (id <N>): Failed to write 'Lock' on id_=<N> ...`, and the session carries on | a status packet on one of the torque writes LeRobot's connect makes (`Lock` or `Torque_Enable`) was lost or came back garbled, and quackd closed the port without writing anything and connected again. Seen three times on 2026-09-23, each on the first connect after the power had been off | nothing, once. The same joint named session after session is a cable to reseat: the one into that servo, and its connectors |
| `lerobot real: connect failed 3 times, the last on <joint> (id <N>): Failed to write ...` | every attempt failed, the last one on that servo. The torque writes may have stopped part way, so some motors can be holding and others limp, which the message says | keep a hand under the arm. Check that joint's cable and connectors, that the servo supply is on, and that nothing else has the port open, then connect again. A message that names no joint says to check the arm's cables and power instead |
| `lerobot real: connect failed 3 times, the last on <joint> (id <N>): FeetechMotorsBus motor check failed on port ...: Missing motor IDs: - <N> ...` | the servo at that address did not answer its ping on any attempt: a cable out, a servo with no power, or one answering with an error such as an overload, which LeRobot lists as missing too. `Motors with incorrect model numbers` in the same place is a servo that answered as another model. LeRobot's handshake reports it before anything is written, so the message says nothing about torque unless an earlier attempt got as far as writing | check that joint's cable and connectors and that the servo supply is on, then connect again |
| `lerobot real: connect failed 3 times: FeetechMotorsBus motor check failed on port ...: Missing motor IDs: - <N> ...` with every motor listed and `Full found motor list (id: model_number): {}` | no servo answered its ping on any attempt. That is what a servo supply that is switched off looks like, which is how the arm is after a power cut, and a cable out between the board and the first servo looks the same, so no joint is named | check that the servo supply is on, then the arm's cables and their connectors, and that nothing else has the port open, then connect again. Nothing was written, so the message says nothing about torque unless an earlier attempt got as far as writing |
| `lerobot real: connect failed 3 times, the last on <joint> (id <N>): Failed to read 'Min_Position_Limit' on id_=<N> ...` (or `Max_Position_Limit`, `Homing_Offset`) | every attempt lost a reply in the calibration check LeRobot's connect makes after the handshake and before `configure()`. It reads and writes nothing, so the message says nothing about torque unless an earlier attempt got as far as writing | check that joint's cable and connectors, that the servo supply is on, and that nothing else has the port open, then connect again |
| `lerobot real: connect stopped after attempt <k> of 3, because a stop was asked for. ...` | a Ctrl-C, or `q`, while the connect was failing. The attempt's own failure follows, LeRobot's words and the joint they name, then that the port was closed without a write and connect was not tried again | nothing to fix for the stop. Read the failure as the rows above, and where the message says some motors may be left with torque on and others off, keep a hand under the arm |
| `lerobot real: connect failed: a LeRobot call (connect) has not come back within 30 s; ...` with `keep a hand under the arm` | the connect ran past its 30 second deadline, or LeRobot timed out itself and its own words follow `connect failed:`. It is never tried again, because its thread may still be on the bus, and it may have stopped anywhere in the torque writes | keep a hand under the arm, and cut its power to let go of it: whatever torque the connect switched on stays on once quackd has exited. Once the process has exited the port is free again; check the USB cable and that nothing else has the port open, then connect again |
| `lerobot real: the arm is not calibrated; run LeRobot's calibration first` | LeRobot read the motors back and they do not match a calibration | run `lerobot-calibrate` under the id quackd will use, and see [the id section](#the-name-you-give-the-arm-is-its-calibration-id) |
| `lerobot real: the arm reports no calibration file, so nothing knows how far each joint travels` | there is no file for this id | the same fix, and check the path `doctor` prints |
| `lerobot real: connect failed once the arm was energised: ... Nothing has read where the arm is, so quackd kept whatever torque connecting switched on rather than let it go where it stands: hold the arm, and cut its power.` | LeRobot's connect went through and switched torque on, and then something that is not one of the refusals above failed: the first read of the joints, the calibration check, or the travel read out of the calibration. LeRobot's own words follow `energised:`. No read has said where the arm stands, so the port is closed with every motor still holding. In 0.14 and before this was left to the disconnect LeRobot makes as the process lets go of the arm, which could drop it | hold the arm, and cut its power. `quackd robot release` connects the same way, so it fails in the same place. Then check the cables and the servo supply for a read that failed, or run LeRobot's calibration again for a calibration quackd could not read the travel out of |
| `lerobot real: only so101_follower is wired` / `this robot has no motors bus` | the config is not an SO-101 follower | quackd drives this one body; an SO-100 shares the calibration directory but is not wired here |
| `lerobot real: --camera-url 'opencv://7' did not open: ...` | the index is wrong, or the camera will not open under this backend | try the index `lerobot-find-cameras opencv` printed, add `?backend=msmf` on Windows, or drop a `width`/`height`/`fps` you pinned. The arm was not touched |
| `lerobot real: --camera-url '...': fps='abc' is not a whole number` | a query key or value quackd does not accept | the message lists every key; this is refused before LeRobot is imported |
| `lerobot real: --camera-url 'opencv://2': it has no ?name= and 2 cameras were given` | several cameras, and one of them is unnamed | add `?name=` to every url. The message shows the shape, `opencv://1?name=top --camera-url opencv://2?name=side`, and says what the name is for |
| `lerobot real: --camera-url 'opencv://2?name=top': name='top' is already the name of 'opencv://1?name=top'` | two cameras with one name | rename one. Two views the model cannot tell apart are worse than one view |
| `lerobot real: --camera-url 'opencv://1?name=side': 1 is already 'opencv://1?name=top'` | the same index given twice | drop the duplicate, or find the other camera's index with `lerobot-find-cameras opencv`. Two handles on one webcam is not two views |
| `microduck:mock takes one --camera-url and 2 were given; only lerobot:real and lerobot:mujoco take several` | a body that reads one camera was handed more | pass one url to that body. Only this arm reads more than one, on the desk or in its simulator, and the message names both rather than opening the first and dropping the rest |

And once it is running:

| What you see | What it means | What to do |
|---|---|---|
| `move_joints: shoulder_pan=170.0 is outside this arm's calibrated range -100.0..100.0` | the goal is outside the travel in your calibration file | aim inside it, or recalibrate if the file does not match the arm's real travel. Nothing was sent. On `wrist_roll` this refusal will never fire, because upstream records a full turn for that joint rather than a sweep |
| `cannot move_joints: elbow_flex reads 61°C: let the arm cool before moving it ...` | the heat gate, below the servo's own 70 °C cut-off | let it cool. A joint that trips its own protection goes slack without announcing it |
| `move_joints: elbow_flex is at 12 with a goal of 45, and it has stopped moving` | a stall: five ticks of 0.1 s in which no watched joint moved more than half a degree (or half the step cap, where that is lower), counted once the move's ramp has handed the servo the goal itself | something is in the way, a mechanical limit the calibration does not know about, or a tripped servo. The arm is held first. A joint blocked partway through a long `duration_s` is only called stalled when that time is up. The same sentence ending `when the time ran out` means the joint was still moving when the verb's limit came: the time asked for, or the time the step cap needs if that is longer, plus 2.5 s, and never more than 18 s. A lowered `QUACKD_LEROBOT_MAX_STEP_DEG` on a long move gets there, and so does a joint creeping under a load |
| `the camera gave no frame: TimeoutError: ... too old` | the webcam stalled or was unplugged | only `observe` is affected, and a `pick` in flight. The arm carries on, and `report_state` starts saying `CAMERA DOWN:` with the reason, so a run that cannot call `observe` still records it |
| `cannot move_joints: the arm's torque is off, so a goal would reach a limp servo` | torque reads off | no verb can toggle torque either way. A fresh connect re-enables it, so torque still off after one points at a tripped servo or the supply. On a `--by-hand` run this is also what the arm reads like between the release and the moment quackd takes hold again, which is before the first turn |
| `cannot place: nothing is held: pick something first` | the `holding` precondition | holding is inferred from the gripper stopping short of shut, so an empty hand reads as nothing held. After a `--by-hand` start it is also what a pilot gets for the pencil you put between the jaws yourself: closing the gripper by hand sets a position and not a grip, and the pilot has to close on the object itself first |
| the run ends saying the arm did not answer | the heartbeat's round trip to the motors failed, and the words after `TimeoutError:` name the call and its budget. Before 0.15.0 there was one more cause: the event loop's thread busy past the deadline, parsing a pilot's first response or encoding a frame, while the arm answered in time, and the answer was thrown away. That was found in the simulator and never measured on an arm. An answer that came back in time is now kept, and a probe queued behind a call which came back in time keeps its place and goes out rather than failing | the cable, the power, or a servo that has tripped. The arm holds its last goal under torque |
| the arm sags when the run ends | no rest pose is recorded, so quackd asks LeRobot's `disconnect()` for the release that is its own default, at the end of every clean session | record one: `quackd robot rest-pose <name>`. Until you do, support it or fold it somewhere it can rest before you exit |
| `the arm is not at its rest pose (...), so torque was left on and it will not fall as it stands: hold it first, because connecting takes torque off every motor for a moment, then run quackd robot release NAME, or quackd doctor --robot NAME to park it, or cut its power` | the arm did not reach the pose you recorded, or the edge of its travel where the pose lies past it, so quackd kept torque rather than dropping it. A run at a terminal offered to release it first, and nobody pressed Enter | hold the arm before anything else, since both commands connect and connecting drops torque for a moment. Then run `quackd robot release NAME` to have it let go into your hands ([Releasing it where it stands](#releasing-it-where-it-stands)), or run `quackd doctor --robot NAME` to let the rest move try again from where it now is, or cut the servo supply. The parenthesis names the joints that fell short |
| `quackd cannot tell whether the arm is holding itself up (the arm did not answer: ...), so it kept whatever torque the arm has: hold it, and cut its power` | the arm did not answer the close's last read, so nothing says where it is or whether its servos are powered. Cutting the supply looks exactly like this, and so does a cable that came out in front of live servos | hold it, and cut the servo supply. No offer is made at the end of a run over an arm that went quiet |
| `torque still reads on for <joints>: cut the power` from `quackd robot release` | those motors kept their torque through the release | cut the servo supply while you hold the arm. The motors not named are limp, and the line after it says what the close then did ([Releasing it where it stands](#releasing-it-where-it-stands)) |
| a run aborts with `the arm did not reach its rest pose: ...` before any model call | the run could not start from the recorded pose | something is in the way, or the pose no longer matches the arm, which is what a new calibration does to a pose recorded before it. Record it again, or move whatever is blocking the fold. A fold past the calibrated travel no longer ends a run here: the arm parks at the edge |
| `<joint> is recorded at <angle> in the rest pose and this calibration lets its servo be driven to <limit> and no further, so it parks there and is let go of there ...` | the fold you recorded lies past the travel in your calibration file, so the arm parks at the edge of it and is let go of there. Not a fault: the run carries on | calibrate again with every joint taken all the way into the fold, then record the pose again ([A pose past the travel](#a-pose-past-the-travel)) |
| `report_state` says `<joint> reads <angle>, past the <limit> its servo can be driven to` | that joint was folded or placed past its travel with torque off, which is where a rest pose past the travel leaves it, or it was parked at its limit and has sagged a few degrees past it under its own weight | nothing. It is said so the pilot does not take the reading for a fault, and goals are still limited to the travel |

### Reported by owners, not by us

One SO-101 has been on a desk here, on 2026-09-15 and again on 2026-09-23, and the list above
is still what quackd's own code does. These are things SO-101 owners report, collected while
writing this page and **not verified against hardware by anyone in this repo**. They are here
because they are the failures that cost an afternoon, not because we can vouch for them.

None of the first three came up on either afternoon: the arm was already assembled, calibrated
and on a working cable, which is the state this page assumes rather than the state a kit
arrives in. The two-camera and the Linux ones could not come up on 2026-09-15, because that
bench ran one webcam on Windows, at 640x480 and with no `?backend=` key needed. What did come
up is an index moving, from the other direction than the bullet describes: a single webcam,
`opencv://1` on one session and `opencv://2` on a later one.

- **No serial port appears at all.** A charge-only USB cable, or the arm's barrel jack out:
  USB does not power the controller board.
- **A fresh kit has every motor on id 1**, so the bus sees duplicates. Upstream's
  `lerobot-setup-motors` walks them one at a time, each motor connected alone and not yet
  daisy-chained.
- **A Waveshare board has two jumpers**, and they belong on the `B` (USB) channel. Upstream's
  own tip is that the power cable can work loose while you handle the board.
- **Two identical webcams swap indices** between boots. On Linux a stable path avoids it
  entirely: `opencv:///dev/v4l/by-id/<device-id>`.
- **Two uncompressed camera streams on one USB controller exceed its bandwidth**, and the
  camera opens and then delivers nothing. `?fourcc=MJPG` is the usual answer.
- **On Linux a pinned size can fail under the default backend** rather than in the camera:
  `?backend=v4l2` is worth trying before you conclude the webcam cannot do the mode.

If you hit one of these, or fail to, that is exactly what the
[checklist](../lerobot-hardware-checklist.md)'s *What to report* is asking for.

## Upstream API

### VERIFIED (read from source at the pin)

| Name | Why quackd relies on it |
|---|---|
| `lerobot` | PyPI and import name |
| `>=3.12` | requires-python; quackd's floor is 3.11, so the extra carries a marker |
| `0.6.2` | the version at the pin; PyPI had 0.6.1 |
| `lerobot[feetech]` | the only home of the serial SDK and pyserial; `quackd[lerobot]` asks for it |
| `lerobot.robots.Robot` | the abstract base every robot implements |
| `Robot.connect(calibrate=True)` | quackd passes False; connect() then writes no calibration into the motors |
| `Robot.disconnect()` | |
| `Robot.get_observation() -> dict` | flat: `'<motor>.pos'` floats plus one array per camera |
| `Robot.send_action(action: dict) -> dict` | `'<motor>.pos'` goals; returns what was actually sent |
| `Robot.observation_features` | camera keys carry shape tuples; usable before connect() |
| `Robot.action_features` | |
| `Robot.is_connected` | |
| `Robot.is_calibrated` | |
| `Robot.calibrate() is interactive` | it calls `input()`; quackd never triggers it |
| `Robot.configure()` | |
| `Robot.__enter__/__exit__` | connect on enter, disconnect on exit |
| `Robot.__del__ disconnects a robot still connected` | a follower collected while still connected is disconnected, and anything that raises is swallowed. So an exit that skipped quackd's close can still end in the follower's `disconnect()`, and what that does to torque is whatever the config holds by then, which is why quackd builds the follower asking to keep it |
| `RobotAction = dict[str, Any]; RobotObservation = dict[str, Any]` | |
| `Robot.calibration` | motor name -> MotorCalibration, loaded from the file; where joint ranges come from |
| `Robot.calibration_fpath` | `calibration_dir / '<id>.json'`; reported so a wrong id is visible |
| `calibrate() records wrist_roll as a full turn` | upstream sweeps every joint except that one and writes 0..4095 for it, so quackd's range refusal is real on four body joints and inert on the fifth |
| `HF_LEROBOT_CALIBRATION/robots/so_follower/` | the default calibration directory: `$HF_LEROBOT_CALIBRATION`, else `$HF_LEROBOT_HOME/calibration`, else `lerobot/calibration` under `$HF_HOME`, which is huggingface_hub's own `$XDG_CACHE_HOME/huggingface` or `~/.cache/huggingface` when unset, and a variable set to nothing still counts as set. The arm simulator walks the same search without importing LeRobot. Two arms sharing an id share a file |
| `a calibration file is draccus JSON of motor name -> MotorCalibration` | one object with a key per motor, each holding exactly the five integer fields. The arm simulator reads it with the json module and refuses a file that names other motors or leaves a field out |
| `draccus>=0.11.6,<0.12.0` | how LeRobot decodes each field of a calibration file: a float is refused and anything else goes through `int()`, so `true` reads as 1 and `"3"` as 3, and a null passes through. The arm simulator decodes the fields the same way, so a twin loads the file its arm loads, and refuses a null, because it builds the travel from every field |
| `lerobot.robots.make_robot_from_config(config)` | |
| `so101_follower` | the registered config type |
| `lerobot.robots.so_follower.SO101Follower` | an alias of SOFollower |
| `SOFollower.name is so_follower` | the calibration subdirectory, shared by SO-100 and SO-101 |
| `SO101FollowerConfig(port, disable_torque_on_disconnect=True, max_relative_target=None, cameras={}, use_degrees=True, position_p_coefficient=16, position_i_coefficient=0, position_d_coefficient=32, num_read_retries=2)` | every safety-shaped field is passed explicitly rather than inherited, and `disable_torque_on_disconnect` is passed as False, the opposite of upstream's default, so a disconnect quackd did not ask for keeps torque |
| `shoulder_pan, shoulder_lift, elbow_flex, wrist_flex, wrist_roll, gripper` | six Feetech sts3215 motors, ids 1..6 in the bus table, which are the ids the arm simulator gives the generic arm it builds when no calibration file is named |
| `'<motor>.pos'` | the observation and action keys |
| `get_observation() reads Present_Position and nothing else` | no torque, current, temperature or fault: why quackd reads registers |
| `camera name -> array` | `cam.read_latest()` under each configured camera's name |
| `max_relative_target caps each step` | clips a goal to present +/- the cap per send_action, reading the cap off the config on every call, which is where a policy segment writes its own |
| `max_relative_target must be a float or a dict per motor` | an int raises; a dict must name exactly the action's joints |
| `ensure_safe_goal_position(goal_present_pos, max_relative_target)` | the whole step cap: a float caps every motor, a dict with other keys than the goal's raises ValueError and anything else raises TypeError, and each goal is clipped to the cap either side of the present reading, which `send_action()` reads just before. A NaN cap caps nothing. The arm simulator reimplements it rather than importing LeRobot, which needs Python 3.12 and torch |
| `send_action() returns the goal actually sent` | the clipped goal, not the measured position |
| `use_degrees=True -> body joints in degrees` | |
| `gripper is 0..100` | whatever use_degrees says |
| `disconnect() disables torque by default` | `disable_torque_on_disconnect` defaults to True, so LeRobot lets the arm go limp at every disconnect, the one it makes of a follower nobody closed included. Under that default an exit that skipped quackd's close, a second Ctrl-C during the rest move or a crash, could drop the arm, so quackd builds the follower with it False and its close writes it every time: True over an arm at its recorded rest pose or with none recorded, which is the limp end of every clean session, a `doctor` probe included, and False over one that did not reach the pose it was recorded resting in. A connect quackd refuses once the arm is energised (not calibrated, no calibration file, no motors bus) writes True before its own disconnect, and one that fails any other way closes the port and keeps torque. Nothing runs when the process is killed |
| `disconnect() reads config.disable_torque_on_disconnect when it runs` | the flag is read off the config instance inside `disconnect()` rather than copied at construction, and the config is a plain dataclass, so the value on the instance when `disconnect()` runs is what it does, whoever calls it. The config is built asking for False and every connect asks again, so a disconnect quackd did not make keeps torque, and quackd writes True immediately before its own only over an arm that may be let go. Read against lerobot 0.6.1, the version the first real arm ran |
| `Max_Torque_Limit 500 on the gripper` | with Protection_Current 250 and Overload_Torque 25: the native authority. The line's own comment calls 500 half the maximum, so the arm simulator gives its model gripper half of that model's force range |
| `the five body joints get no torque or current cap` | the caps sit inside a check for the gripper's name |
| `configure_motors() writes Return_Delay_Time 0 and Acceleration 254` | called inside torque_disabled(), so connecting drops torque briefly |
| `SOFollower.is_connected is the serial port plus the cameras` | |
| `SOFollower.connect() refuses while the port is open` | `check_if_already_connected`, and `is_connected` is the port's flag. connect() opens the port first and configures last, and nothing shuts the port when configure() raises, so a retried connect has to close it first (`MotorsBus.disconnect(False)`) |
| `configure() switches torque off and on again with no retry` | `torque_disabled()` calls `disable_torque()` and `enable_torque()` with `num_retry` 0, each a `Torque_Enable` then a `Lock` write per motor, so one status packet lost or garbled fails the whole connect with the motors in two torque states. The bench arm did this on three connects on 2026-09-23; quackd connects again, up to three attempts |
| `SOFollower.bus is a FeetechMotorsBus` | the attribute registers are read through |
| `no deadman: nothing stops the arm when the client goes quiet` | the class has no thread, timer or timeout; a goal stands until the next write |
| `RobotKinematics sets a URDF joint to np.deg2rad(degrees)` | LeRobot's own kinematics helper puts a reading on a model of the arm in radians with no offset and no sign, in forward and inverse kinematics alike. The arm simulator follows it for the five arm joints, which is what [`JOINT_ZERO` and `JOINT_SIGN`](#unverified-our-assumptions-about-the-model-and-what-quackd-does-about-each) assume |
| `MotorsBus.disable_torque()` | never called on quackd's own initiative. The one call is `let_go()`, and it has two doors, each opened by a person at a terminal. `let_go()` is `quackd run --by-hand`'s, and refuses anywhere but the arm's recorded rest pose, the same condition `close()` uses to decide that letting go will not drop it. `let_go(anywhere=True)` is `quackd robot release`'s and the end-of-run offer's, after each has told the person to hold the arm, and releases wherever the arm stands, with or without a rest pose recorded. No verb reaches either and no model can ask for it. On a Feetech bus it writes `Torque_Enable` 0 then `Lock` 0 per motor, and quackd asks for `num_retry=5`, the count upstream's own `disconnect()` uses |
| `MotorsBus.enable_torque()` | called by `take_hold()`, to pick up an arm a person has just placed, with `num_retry=5` as for the release. It writes `Torque_Enable` 1 **and then `Lock` 1** per motor, two writes a motor rather than one |
| `MotorsBus.disconnect(disable_torque=True)` | the `disable_torque()` call is inside `if disable_torque`, so False closes the port and leaves every motor holding the goal it was last written: what an arm that missed its rest pose gets instead of falling, and how the port is closed between two connect attempts without a write to any motor. The same branch clears the port handler's busy flag (`port_handler.is_using = False`, motors_bus.py lines 557 and 558), which a serial error in the middle of a packet leaves set and which reopening the port does not clear, so quackd clears it after its own close: without that, every packet of the next attempt is answered "port in use" and the connect is refused as every motor missing |
| `MotorsBus.motors: name -> Motor(id, model, norm_mode)` | the table that gives each servo its bus address; a bus error's id is turned into a joint through it, never through an assumed order |
| `Failed to write '<register>' on id_=<N> with '<value>' after <k> tries. <result>` | what a single write or read that failed raises. quackd reads the id out of it to name the joint; a sync read or write names several and quackd names none |
| `_handshake` | what `MotorsBus.connect()` runs once the port is open: a ping per motor and the firmware reads, and no write. `configure()`, where every write of a connect is, comes after it, so a connect refused in the handshake left every motor's torque as it was, and its refusal does not tell you to keep a hand under the arm. quackd tells the two apart by this frame in the error's traceback |
| `Missing motor IDs: / Motors with incorrect model numbers: - <N> (...)` | what the handshake raises for a servo that did not answer its ping, or answered as another model: one line per motor. A servo answering with its error bit set, an overload say, is listed as missing too. quackd names the joint of the first id listed, through the bus's motor table |
| `Failed to sync read '<register>' on ids=[<N>, ...] after <k> tries. <result>` | what a read of several motors at once raises when no good reply came back, and a sync write says `Failed to sync write` when its packet could not go out: it waits for no reply, so a lost one cannot fail it. The result is the servo SDK's own words, `[TxRxResult] There is no status packet!` for a reply that never came. Neither names one motor, so quackd names no joint. The arm simulator fails its reads and goal writes in these words |
| `sts3215 model number 777` | what an SO-101 servo answers a ping with, and what the handshake prints beside each id it lists. The arm simulator's handshake fault prints it where the arm's would |
| `check_if_not_connected refuses a call on a port that is not open` | ``<class> is not connected. Run `.connect()` first.``, a ConnectionError, on the follower's observation, send and disconnect and on every bus read and write, and its twin says `<class> is already connected.` for a connect. The arm simulator refuses in the same words under upstream's class names |
| `MotorsBus.is_connected is port_handler.is_open` | a port flag, not a reply: why the heartbeat reads the arm |
| `FeetechMotorsBus.is_calibrated reads the motors back` | a missing, stale or foreign file all read as not calibrated |
| `write_calibration() is reached only through calibrate()` | quackd cannot move an arm's zero by accident |
| `MotorsBus.sync_read(data_name, motors=None, normalize=True, num_retry=0)` | one transaction for every motor named |
| `NORMALIZED_DATA is Goal_Position and Present_Position` | every other register comes back raw |
| `MotorCalibration(id, drive_mode, homing_offset, range_min, range_max)` | raw encoder ticks, not degrees |
| `Invalid calibration for motor '<motor>': min and max are equal.` | LeRobot loads a file whose range_min equals its range_max and refuses the first reading or goal through that motor. The arm simulator refuses the file as it reads it |
| `degrees = (raw - mid) * 360 / 4095` | how a calibration file becomes a range in degrees, centred on zero |
| `a degrees goal is not clamped to the calibrated range` | the two 0..100 modes are clamped and DEGREES is not, so the servo's own clamp is what stops a goal past the travel, silently: why quackd refuses |
| `write_calibration() writes Min_Position_Limit and Max_Position_Limit, and the servo clamps Goal_Position to them` | the servo clamps every goal to the calibrated travel and never a reading, seen on an SO-101 on 2026-09-23: why a rest pose is clipped into the travel and a joint past it gets no goal |
| `sts3215 resolution 4096` | one tick is about 0.088 degrees |
| `Torque_Enable (40, 1) and Present_Temperature (63, 1)` | the two registers quackd reads; upstream reads neither |
| `Camera.async_read(timeout_ms)` | the most recent new frame |
| `Camera.read()` | |
| `OpenCVCamera converts BGR to RGB when color_mode is RGB` | channel order is a config choice |
| `OpenCVCameraConfig.color_mode defaults to ColorMode.RGB` | quackd passes RGB explicitly anyway |
| `lerobot.cameras.opencv.OpenCVCamera(config)` | the camera quackd builds and owns, beside the follower |
| `OpenCVCameraConfig(index_or_path, fps=None, width=None, height=None, color_mode=ColorMode.RGB, rotation=Cv2Rotation.NO_ROTATION, warmup_s=1, fourcc=None, backend=Cv2Backends.ANY)` | what `--camera-url`'s query keys fill in |
| `lerobot.cameras exports Camera, CameraConfig, ColorMode, Cv2Backends, Cv2Rotation` | the config is deliberately not among them, so quackd imports from both |
| `Cv2Backends: ANY, V4L2, DSHOW, PVAPI, ANDROID, AVFOUNDATION, MSMF` | the backend is a config field, so `?backend=msmf` needs no patched source. `--camera-url` takes the five that name a platform you could be on; the other two would only ever be a refusal |
| `Camera.connect(warmup=True)` | it reads frames before returning, so a camera that opens and never delivers fails here |
| `connect() raises ConnectionError on an index that will not open` | quackd passes its words through, and they name `lerobot-find-cameras opencv` |
| `a requested fps or size that the camera refuses raises RuntimeError` | why quackd asks for no mode unless you name one |
| `an unset fps, width or height keeps the camera's own mode` | what makes an unknown webcam in a lab drawer work |
| `Camera.read_latest(max_age_ms=500)` | the newest buffered frame; it raises when the camera has stalled, and `get_frame` turns that into a reason |
| `Camera.disconnect()` | |
| `a follower's cameras are part of its connected state` | `is_connected`, `send_action` and `disconnect()` all include them, which is why quackd's camera is not the follower's |
| `lerobot-find-cameras opencv` | how an owner learns which index is which: it saves a frame per camera |
| `lerobot-find-port` | upstream's own port finder: it names the port that disappears when you unplug the arm, which is the only way to be sure which one it is |

### UNVERIFIED (our assumptions, and what quackd does about each)

A policy's names, and the one assumption about running one, are in
[the policies' table](#the-policies-upstream-lerobot-061) below, read at the version a policy
server runs.

| Name | What quackd does |
|---|---|
| `TORQUE_ENABLE_HOLDS_PRESENT` | what a servo does with the goal it was last told when torque comes back on. `enable_torque()` writes `Torque_Enable` and then `Lock` on each motor, neither of them a goal, so whether the motor then holds where it is or drives to that stale goal is the firmware's business and is documented nowhere quackd can read. It matters because the goal last written before a hand-off is the rest pose the arm has since been lifted out of by hand, so a snap back to it would happen with somebody's fingers in the way. `take_hold()` writes the present position as the goal **before** enabling torque, writes it again after, and reads the arm back to check it stayed, so the assumption is never relied on in either direction. A joint placed past its calibrated travel is where that cannot work: a goal written there is clamped to the limit (`POSITION_LIMITS_CLAMP_GOALS`), and no goal leaves the servo the last one it had, the rest move's, which only this row could say it ignores. So `take_hold()` leaves torque off and refuses while any body joint reads outside its travel |
| `GRIPPER_OPEN_VALUE` | 100 is assumed open; which end is open is how the arm was calibrated, and the checklist asks for it by hand |
| `HOLDING_INFERRED` | holding is the gripper told to close, settled, and short of shut; listed in `extras.assumptions`. A gripper a person closed by hand is a position and not a grip, so an arm placed with `--by-hand` reports nothing held until the pilot closes the gripper itself |
| `TEMPERATURE_C` | the register is read raw and treated as Celsius; the 60 °C refusal and the 70 °C cut-off are Feetech's numbers, not measured |
| `JOINT_RANGES` | each joint's travel is computed from the calibration file and a goal outside it is refused. It is not the mechanical limit on every arm: a calibration that never saw a joint folded all the way leaves the fold past it, as the bench arm's did on 2026-09-23, which is why a rest pose is clipped into the travel. Whether it is the mechanical limit on any given arm is unverified |
| `SERIAL_PORT` | `--address` is checked for shape and nothing more |
| `THREAD_SAFETY` | every call is serialised under one lock in a worker thread with a deadline; a blown deadline wedges the transport |
| `CAMERA_INDEX_MOVES` | an index is a scan position, not an identity: it can move on a replug or a reboot, and a laptop's own webcam usually holds 0. quackd records the index it opened and cannot tell you it is the camera you meant |
| `WINDOWS_CAMERA_BACKEND` | which backend a Windows machine needs for a given webcam is not knowable in advance, so quackd keeps upstream's ANY and gives the owner `?backend=msmf` |

## The simulator's upstream: SO-ARM100

[The simulator](#the-simulator-lerobotmujoco), `lerobot:mujoco`, is the `real` backend's own
code over a physics model of the SO-101, and this is where that model comes from. The model is
set in a scene of quackd's own, a table, lights, the cameras and the objects on it, and CI runs
the same code over a primitives-only stand-in arm (`sim/standin.py`), because nothing a pull
request waits on fetches the model. The nightly `lerobot-sim-assets` job fetches it the way a
first run does and runs the sweeps on it.

The follower carries exactly what the `real` backend reads and writes on a LeRobot follower,
and does under it what LeRobot and the servo do with a goal. It reads in whole encoder ticks
through the calibration, caps each send at the step as LeRobot does, clamps a goal to the
calibrated travel as the servo does, without a word, and lets a limp joint keep its goal until
torque drives it there. Its faults are
seeded, and each is raised in LeRobot's own words from a function named as LeRobot's, so the
`real` backend says of it what it would say of the arm. `tests/test_lerobot_sim_parity.py`
runs the `real` backend over it and over the fake arm its own tests use, side by side.

The model is the maker's own, [TheRobotStudio/SO-ARM100](https://github.com/TheRobotStudio/SO-ARM100),
pinned at
[`5f6d2b8`](https://github.com/TheRobotStudio/SO-ARM100/tree/5f6d2b876a53a4872e405b991dd925556c9e38a4)
(`main`, 2026-09-23) and read on 2026-09-26. Every name quackd spells from it lives in
[`adapters/lerobot/src/quackd_lerobot/sim/upstream_api.py`](../../adapters/lerobot/src/quackd_lerobot/sim/upstream_api.py).

**The model and its meshes are fetched at run time and never shipped.** Neither the wheel, the
repository nor a test fixture carries a byte of them, although their Apache-2.0 licence would
allow it, because no upstream asset is ever committed here.
[`sim/assets.py`](../../adapters/lerobot/src/quackd_lerobot/sim/assets.py) fetches
`so101_new_calib_camera.xml` and the meshes it names one file at a time from
raw.githubusercontent.com at the pin, rather than the whole repository, and checks each one
against the sha256 recorded for it. Only once every file matches does it install the set in
`~/.quackd/cache/so-arm100/<pin>/`, with a licence notice beside it. `QUACKD_CACHE_DIR` moves
the cache. `QUACKD_LEROBOT_SIM_ASSETS` points at the `Simulation/SO101` directory of a checkout
of your own instead. A file there that differs from the pin is a warning rather than an error,
because a newer model is what a checkout is for, and the model is then reported as not pinned.
Line endings in the model do not count, because Git for Windows checks it out with CRLF.

### VERIFIED (read from the model and its README at the pin)

| Name | Why quackd relies on it |
|---|---|
| `the repository's LICENSE is the Apache License 2.0` | no other licence file sits beside the simulation files and neither README names one, so the model and its meshes are under it. quackd fetches them rather than shipping them all the same |
| `so101_new_calib_camera.xml` | the model quackd loads: new_calib, the default calibration, plus upstream's wrist camera mount. It includes no other file, so it needs nothing of upstream's but its meshes |
| `no <option>, <camera>, <light> or table in the model` | the arm and nothing around it, so the physics settings, the table, the lights and every camera the simulator renders from are quackd's and not upstream's. Upstream's `scene.xml` wraps the variant without the camera mount, and quackd does not use it |
| `<compiler angle="radian" meshdir="assets" autolimits="true"/>` | every range in the file is in radians, and quackd reads each one from the loaded model rather than copying a number out of the file |
| `15 STL meshes under Simulation/SO101/assets` | every mesh the model names and nothing else from that directory, each fetched on its own and checked against its hash |
| `wrist_camera_mount and wrist_camera` | two bodies under the gripper, meshes only, with no camera element in either: where a wrist view is rendered from is quackd's |
| `the fingers are part of the meshes wrist_roll_follower_so101_v1 and moving_jaw_so101_v1` | the fixed finger is one printed part with the wrist housing, and each part collides as the whole of its mesh. MuJoCo collides a mesh as its convex hull, so the fixed part's hull fills the opening the other finger closes into and throws a pen out of the grasp. quackd turns both off and collides the two fingers and the palm as boxes cut from the meshes' own vertices when the model loads |
| `joints and actuators named shoulder_pan, shoulder_lift, elbow_flex, wrist_flex, wrist_roll, gripper` | exactly LeRobot's motor names in LeRobot's order, so a `'<motor>.pos'` key reaches its joint and its actuator by name |
| `new_calib: each joint's zero is the middle of its range` | the README's words, which the file bears out for the five arm joints: four ranges are symmetric about zero, and `wrist_roll`'s middle is a few degrees from it. LeRobot's degrees for those joints are centred on the middle of the calibrated travel too, which is why the two zeros are expected to meet. Not for the gripper: its hinge has its zero near one end of its range and LeRobot's gripper is 0..100, so it is `GRIPPER_MAP`'s |
| `<position kp="998.22" kv="2.731" forcerange="-2.94 2.94"/>` | the gains every joint uses, which the file says were calculated following [RBE501-RL-arm-project](https://github.com/Gregory119/RBE501-RL-arm-project/blob/main/gymnasium_env/README.md) with the servo's proportional gain assumed to be 16, LeRobot's default, and which it says are not a one to one mapping of LeRobot's servo gains. Each of the six actuators overrides the force range with -3.35 3.35. quackd uses them as written |
| `STS3215 motor properties adapted from the Open Duck Mini project` | the README's account of where the servo properties in the model came from: another robot |
| `LeRobot's gripper 0..100 is not yet reflected in the URDF and MuJoCo files` | the model's gripper is a hinge in radians, so the map from LeRobot's 0..100 is quackd's (`GRIPPER_MAP`) |

### UNVERIFIED (our assumptions about the model, and what quackd does about each)

| Name | What quackd does |
|---|---|
| `SERVO_DYNAMICS` | the gains, damping and friction are a calculation and another robot's properties, not a measurement of an SO-101. quackd treats the simulated dynamics as the model's and never as the arm's: a settle time, a push or a grasp that holds in the simulator is evidence about the model, and only the bench can say it about an arm |
| `JOINT_ZERO` | whether a real arm's calibrated middle of travel is the model's zero, on the five arm joints. A calibration records the travel one person swept on one arm, and nothing says that matches the CAD. quackd assumes an offset of zero on each of them until the bench measures one, as LeRobot's own kinematics helper does (`RobotKinematics sets a URDF joint to np.deg2rad(degrees)`, in the LeRobot table above). The gripper is `GRIPPER_MAP`'s |
| `JOINT_SIGN` | whether a positive degree turns the model's joint the positive way. That depends on how each servo was mounted and calibrated, which the model cannot know. quackd assumes it does on the five arm joints, as LeRobot's kinematics helper does. Which end of the gripper is closed is `GRIPPER_MAP`'s, found from the model |
| `GRIPPER_MAP` | LeRobot's 0..100 is mapped linearly over the model's gripper hinge, with the closed end found from the loaded model rather than assumed, and a reading is clipped to 0..100 |
| `WRIST_CAMERA_POSE` | whether upstream's printed mount sits where your wrist camera sits. quackd renders the wrist view from the mount as the model places it and never claims it is your camera's view |

## The policies' upstream: LeRobot 0.6.1

`pick` and `manipulate` hand the arm to a learned policy, and a quackd command loads a LeRobot
checkpoint only in [a policy server](#a-policy-in-a-process-of-its-own-quackd-policy-serve),
never in the process that owns the arm's bus. Only `load_policy()`, which nothing in quackd
calls, would load one there (`LOAD_POLICY`, below). The names quackd relies on here are read
against lerobot 0.6.1, the version the laptop that drives the lab arm runs, at the commit its
tag names,
[`7e241bd`](https://github.com/huggingface/lerobot/tree/7e241bd630a3719a56157a497ce5d08f244784f1)
(`v0.6.1`), and were read on 2026-09-27, rather than at the `main` commit
[the arm's own table](#upstream-api) is pinned to. The installed 0.6.1 wheel and the tag were
compared file by file that day, and every file these rows cite was the same. Every name lives in
[`adapters/lerobot/src/quackd_lerobot/policy/upstream_api.py`](../../adapters/lerobot/src/quackd_lerobot/policy/upstream_api.py).

**No trained checkpoint has been loaded by quackd in CI or on a GPU.** CI's `policy` job loads a
tiny random ACT through the real server, on the CPU and offline, which is what `POLICY_PIPELINE`
below rests on. Trained checkpoints have been loaded only on one laptop's CPU, where an ACT from
the Hub drove a twin of the lab's arm on the simulator and `lerobot/smolvla_base` never answered
a step in time ([policies.md](../policies.md#smolvla-and-act)). pi05 and tick mode have not run
at all.

### VERIFIED (read from source at the v0.6.1 tag)

| Name | Why quackd relies on it |
|---|---|
| `lerobot.policies.pretrained.PreTrainedPolicy` | |
| `lerobot.configs.policies.PreTrainedConfig` | a checkpoint's own config, read before the policy class is built; the one policy name that is not in the factory |
| `a policy's config carries no fps` | the rate a policy runs at is the fps of the data it learned from, so the server takes a rate it is given and never guesses one, and the rate travels with where it came from |
| `PreTrainedPolicy.from_pretrained(path, *, config=None, local_files_only=False, revision=None, strict=False)` | a local directory or a Hub repo id, at a revision, and the policy comes back in eval mode. Left lenient, a weight the file lacks is only logged and the model keeps what it was built with, so the server asks for `strict=True` and refuses a checkpoint whose weights are not its model's |
| `PreTrainedPolicy.select_action(batch: dict[str, Tensor]) -> Tensor` | one action per call |
| `PreTrainedPolicy.predict_action_chunk(batch: dict[str, Tensor]) -> Tensor` | the whole chunk for one observation, which is what a step is answered with |
| `PreTrainedPolicy.reset()` | |
| `lerobot.policies.factory.get_policy_class(name)` | |
| `lerobot.policies.factory.make_pre_post_processors(policy_cfg, pretrained_path=None, pretrained_revision=None)` | the observation and the action tensor each go through one, loaded at the revision given |
| `lerobot.policies.factory.make_policy(cfg)` | |
| `lerobot.policies.utils.build_inference_frame(observation, device, ds_features, task, robot_type)` | picks the keys `ds_features` names out of a raw observation, which is why a reset declares the motors in the bus's order and each camera |
| `lerobot.policies.utils.make_robot_action(action_tensor, ds_features)` | one action row to a dict by name, so a chunk is turned into actions a row at a time |
| `ACTConfig.chunk_size and n_action_steps` | how many actions one inference predicts and how many of them are played, 100 and 100 for ACT and 50 and 50 for SmolVLA. The server reports both |
| `ACTConfig.temporal_ensemble_coeff` | set, ACT is asked every step with `n_action_steps` 1, which is the loop's tick mode |
| `a processor step named by class is imported by its module path` | a step without a registry name is imported from whatever module its `class` key names, so loading a checkpoint's processors can run any code the checkpoint points at: why no quackd command loads a checkpoint beside the arm's bus. Only `load_policy()` would (`LOAD_POLICY`), and nothing in quackd calls it |
| `ActionTokenizerProcessorStep.trust_remote_code defaults to True` | the action tokenizer trusts a repository's code unless told not to, the observation tokenizer loads one by name at no revision, and SmolVLA names its backbone by an unpinned Hub name. The server allows no action tokenizer, tells any step that could trust remote code not to, and pins every such name at a commit or a tag or refuses it. A SmolVLA config with no backbone gets LeRobot's default, so it is refused. SmolVLA's build asks transformers for its backbone without saying `trust_remote_code` either way, so a pinned model whose files map a class to code, or whose cached directory holds anything but configs, tokenizer files and safetensors, is refused |
| `config.json, model.safetensors, policy_preprocessor.json, policy_postprocessor.json, train_config.json` | the files a checkpoint is read from, each fetched at the revision named, the config and the processors before anything else, and nothing else of the repository |
| `train_config.json's dataset.repo_id and dataset.revision; meta/info.json's fps` | where a checkpoint's rate is read when the server is given no `--fps`, and only at a commit or a tag, since the checkpoint chose that revision and a branch would give a rate that could change between two serves |
| `observation.state, observation.images.<name>, action` | the keys a policy's state, images and action go by. An image's frame is handed over under its key with `observation.images.` cut off, which is why each camera is the image of its own name |
| `FeatureType: "STATE", "VISUAL", "ENV", "ACTION", "REWARD", "LANGUAGE"` | a checkpoint whose inputs are anything but one state and some images, or whose output is anything but one action, is refused, since an arm has nothing to give the rest from |
| `"act", "smolvla", "pi05"` | the types whose processors the allowlist was read from, and any other is refused before its weights are fetched |
| `rename_observations_processor, to_batch_processor, device_processor, normalizer_processor, unnormalizer_processor` | the steps every policy's processors are built from, and all of ACT's |
| `smolvla_new_line_processor, tokenizer_processor, pi05_prepare_state_tokenizer_processor_step, relative_actions_processor, absolute_actions_processor` | the steps SmolVLA's and pi05's pre-processors add. With the row above, the only steps a checkpoint may name |
| `overrides={"device_processor": {"device": device}}` | how upstream's own loops put a loaded pre-processor on their device. The server does the same and puts the post-processor's on the CPU, where the answers are read |
| `lerobot.utils.device_utils.auto_select_torch_device()` | the device a checkpoint is loaded on, and whether the server says it has a GPU |
| `DataProcessorPipeline.reset()` | the policy and both processors are reset at every session's reset, as upstream's own loop does |
| `NormalizerProcessorStep.state_dict() -> {'<feature>.<stat>': Tensor}` | where the state's and the action's 1st and 99th percentiles are read, which the arm holds against its travel |
| `PI05Config.action_feature_names, a list of names or None` | the action's dimensions by name, where a checkpoint has them, which the arm checks against its bus |
| `SmolVLA and pi05 run with some of their images missing` | an ACT takes every image it names, so the arm refuses one with an image no camera is mapped to, and says which of a SmolVLA's or a pi05's go padded |
| `ACTConfig.pretrained_backbone_weights = "ResNet18_Weights.IMAGENET1K_V1"` | torchvision would fetch these as ACT is built, before the checkpoint's own weights replace them, so the server sets it to None |
| `RelativeActionsProcessorStep caches the state each time the pre-processor runs` | training makes a chunk relative to one state, and the absolute step adds back the state cached last. LeRobot's own loop runs the pre-processor every tick, so each action it plays from its queue is made absolute against the state of the tick it is played at. The server makes a whole chunk absolute against the state it was predicted from, as OpenPI does, and serves such a pi05 in chunks |
| `PI05Policy.from_pretrained returns the model without its weights when they do not load` | pi05's own loader catches a load that fails, prints a line and hands back the model as it was built, a random network. The server loads a pi05's weights the way that loader does, its key fixes and a `model.` prefix, strictly and with nothing caught |
| `async inference unpickles what it is sent` | LeRobot's own policy server and robot client `pickle.loads` what they receive, over an insecure port, which is why quackd has a protocol of its own |
| `the async robot client imports torch` | and the arm's process is the one quackd keeps free of torch |
| `observations_similar(obs1, obs2, lerobot_features, atol=1)` | the async server skips an observation near the last one it ran, and quackd's runs every step it is asked |
| `SUPPORTED_POLICIES = ["act", "smolvla", "diffusion", "tdmpc", "vqbet", "pi0", "pi05", "groot"]` | the policies the async server will load, and anything else is refused there |
| `POLICY_PIPELINE: build_inference_frame, the pre-processor, predict_action_chunk, the post-processor over the chunk, make_robot_action a row at a time` | exercised, not only read: CI's `policy` job serves a tiny random ACT from a Hub cache at a commit and a tag, offline and with torchvision unable to fetch the backbone its config names, through the real server and client and an arm behind them, and checks every action of a chunk against what LeRobot's own `select_action` plays from the same files (`tests/test_policy_pipeline.py`). ACT on the CPU and nothing else, and a random one, so nothing about what a trained policy does to an arm follows from it |

### UNVERIFIED (our assumptions about policies, and what quackd does about each)

| Name | What quackd does |
|---|---|
| `VLA_PIPELINE` | that SmolVLA and pi05 run through the same pipeline as ACT: their tokenizer step, the nested models pinned with `--pin`, the missing images padded, their weights loaded strictly (a SmolVLA's by LeRobot's own loader, a pi05's by quackd's copy of its loader), so a checkpoint saved with weights its model does not have would be refused, and pi05's relative actions made absolute over a whole chunk, against the state it was predicted from. That last one is not what LeRobot's own loop does (`RelativeActionsProcessorStep caches the state each time the pre-processor runs`), and it is how training made the chunk relative, so a trained pi05 is taken to want it. No job runs them, since neither the lab's environment nor CI installs transformers. They load through the same allowlist, pins and refusals as ACT, and a first run belongs on the simulator after `quackd policy check --bench` |
| `TICK_MODE` | that an ACT with temporal ensembling, asked through `select_action` every tick, answers what upstream's own loop would play. The server refuses one without a GPU, so CI's CPU job cannot run it, and no bench has |
| `LOAD_POLICY` | `load_policy()` in the arm's backend builds a policy in the arm's own process, the one no checkpoint is to load in, and hands the pre-processor a raw observation `build_inference_frame` would have shaped first. Nothing calls it and nothing has run it. What reaches the arm from any policy is quackd's rule: the verbs' step cap, and a goal outside the travel clipped and counted, unlike a verb's goal, which is refused |

## Status

`lerobot:mock` runs every arm verb through the executor in the test suite, including the
confirm gate on `pick`, the `holding` precondition on `place` and the heat refusal.
`lerobot:real` is exercised against a fake arm and a fake policy (verified method names, no
serial port), and it has now run on one real arm, on 2026-09-15 and again on 2026-09-23.

The 2026-09-23 afternoon ran quackd 0.12.0, so what quackd does about that afternoon has not run
on an arm yet: parking a rest pose at the edge of its travel, `stop` leaving out a joint that
reads past its travel, `quackd robot release` and the offer at the end of a run, the connect's
retries, a `move_joints` paced over its `duration_s`, and the take-hold refusing a joint past
its travel. The [checklist](../lerobot-hardware-checklist.md) is the order to find out in.

`lerobot:mujoco` runs that same code over [the simulator](#the-simulator-lerobotmujoco): in CI
on a primitives-only stand-in arm, and on the maker's model in sweeps run by hand on 2026-09-27,
which the nightly `lerobot-sim-assets` job is there to repeat. On that model a grasp driven
through the real backend's own verbs lifts a cube clear of the table between both finger pads on
ten seeds of ten, judged by the world's truth, and `quackd preflight` passes the bundled
`lerobot-lookout` on the generic arm, which has no rest pose to return to, and a grasp task
with a sidecar from a rest pose its close has to reach, each on ten seeds of ten. The job's
first run on GitHub, dispatched on `main` on 2026-09-29 at the commit tagged `v0.16.0`, passed
all three ten of ten
([run 36523568197](https://github.com/rokbenko/quackd/actions/runs/36523568197)). That is the
simulator doing what it says, and nothing about the arm: no run on the simulator has been
compared against one, and the ✅ it carries never raises `lerobot:real`'s.

### What one afternoon proved, and what it did not

On 2026-09-15 an SO-101 follower, calibrated as `arm-01` and reached as `--robot lerobot:real
--address COM3` with no registered name, ran on Windows 11 with
Python 3.12.12, lerobot 0.6.1 and quackd 0.9.0, piloted by OpenAI's `gpt-6-astra`. This is the
only one of quackd's seven bodies that has been on hardware at all.

What ran:

- `lerobot-lookout`, with a real pilot and once with `--llm fake`, which is how you tell
  an arm that will not answer from a model that will not decide.
- Free-form `--goal` runs. It waved by rolling the wrist about 27 degrees either way, waved
  again from an extended pose with `shoulder_lift` at -39 and `elbow_flex` between 24 and 30,
  and opened and closed the gripper: commanded 100, reported 98 open and 3 closed with the jaws
  nearly touching. One run mimed a duck quacking with the gripper.
- A USB webcam at `opencv://1`, and at `opencv://2` on a later session, 640x480, with no
  `?backend=` key needed on that machine.

What went wrong, which belongs in the same breath as the above:

- **The arm fell at the end of every run**, because `disconnect()` drops torque. That is the
  whole reason [the rest pose](#the-rest-pose) exists, and the rest pose is the one thing on
  this page written after hardware rather than before it. It met the same arm on 2026-09-23 and
  could not reach a fold that lay past the calibrated travel, which is the box at the top of
  that section.
- One dry run aborted with `the arm did not answer: TimeoutError` when a single heartbeat round
  trip failed. It never recurred. One way that line could appear was found later, in the
  simulator: the event loop's thread busy past the heartbeat's deadline while the arm's answer
  was already in, which 0.15.0 fixed. Nobody measured it on the arm, so it is one cause that
  exists and not the explanation of that day.
- One dry run aborted because the pilot answered `uncertain` at `assess_task` and the human
  said no. That is the gate working, not a fault.
- The camera framed the gripper and cropped the raised arm, so the model verified its own waves
  from joint readings rather than from the picture. A view that shows the whole arm is the
  first thing the second bench should fix, and it is now two `--camera-url` flags rather than a
  choice between views.

What nobody measured, and what this page therefore still cannot tell you:

- whether the `holding` band is right, or whether an empty hand that binds reads the same as a
  grip
- what a joint actually reads after ten minutes of work, so whether the 60 °C refusal fires
  before anything is hot enough to matter
- whether a stall is caught on purpose. Nothing was deliberately obstructed
- whether 5 degrees an action felt right in the room, which is the one number on this page that
  only a person standing next to the arm can judge

No policy was loaded that afternoon, or on 2026-09-23. `pick`, `manipulate` and the policy
server postdate both, and none of them has driven an arm.

## How to help

If you have an SO-101 on a desk, work through
[lerobot-hardware-checklist.md](../lerobot-hardware-checklist.md) in order: nothing moves
until step 10. `lerobot-lookout` is the first task to point at it; it asks for `report_state`
rather than `observe`, because a `.duck` is checked against the static manifest, which
cannot know whether you brought a webcam. What most needs a real arm is that checklist's
*What to report*, and the four unmeasured things above are the top of it. One afternoon on one
arm is a sample of one: what differs on yours is the part worth writing down. Open an issue
with the transcript.

A task file that passes `quackd preflight` on your arm's twin and then does something else on the
arm is worth an issue of its own, with both transcripts. Nobody has compared the simulator
against an arm yet, and that comparison is the only thing that can move its assumptions from
UNVERIFIED.
