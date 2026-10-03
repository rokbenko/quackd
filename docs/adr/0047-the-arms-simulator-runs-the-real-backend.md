# ADR-0047: The arm's simulator runs the real backend

**Status:** accepted · **Date:** 2026-09-27 · Amends [ADR-0030](0030-mujoco-physics-backend.md) (whose MuJoCo backend was the Microduck's alone) and [ADR-0016](0016-flock-lockstep-clock.md) (the lockstep clock, now with a participant per sleep, and the waiter fix found on the way) · Extends [ADR-0036](0036-what-the-arm-does-not-say.md) and [ADR-0045](0045-a-rest-pose-the-calibration-cannot-reach.md) (what the simulator reproduces of the arm's servos, and what it only assumes) · Implemented in `adapters/lerobot/src/quackd_lerobot/sim/`, `quackd/preflight.py`, `quackd robot twin` in `quackd/cli.py`, and `.github/workflows/lerobot-sim-assets.yml` ([page](../adapters/lerobot/README.md#the-simulator-lerobotmujoco), [first run](../adapters/lerobot/first-run.md#16-between-visits-rehearse-on-the-simulator))

## Context

The SO-101 went back to the lab on 2026-09-23 with task files written for that afternoon, and
most of what went wrong there was not the pilots. Nineteen of the 26 runs never moved the arm at
a pilot's request (CHANGELOG, 0.14.0). The rest pose lay past the travel the arm's calibration
recorded and the servo would not go there. A connect failed on one lost status packet. A stop
hauled a folded joint up to its limit. Pilots shown a joint past its travel declined to move an
arm they could not explain. Each of those was decided by the code between the pilot and the
bus, and every one of them was found at the bench, where finding it costs an afternoon with
somebody else's arm.

quackd could not rehearse any of it at home. `lerobot:mock` is the same verbs over an arm of its
own: a goal lands the instant it is sent, nothing is ever lost on a bus, and the refusals it
mirrors are written again in the mock rather than run from the real backend, whose code it
never touches. The cartoon has no arm at all. `microduck:mujoco` is physics, but the
duck's, and [ADR-0030](0030-mujoco-physics-backend.md) built it as the duck's own package with
one world and one body. What a rehearsal of an arm needs is the code that drives the arm, run
over something that behaves like one.

The plan for this was checked against the code, against LeRobot 0.6.1 as the lab ran it and
against upstream before anything was written, and several of its first answers did not
survive that:

- Swapping the two builders that make the follower and the cameras is not enough by itself. The
  real backend also paces itself on a clock, names itself in every refusal, and meets a person
  at the arm in `--by-hand`, and each of those needs an answer on a simulator.
- A clock keyed on asyncio tasks freezes, and so does one gate that a single task holds while
  others ride on it. `FlockClock.sleep(pid, 0)` registers an id and never lets it go.
- `--live` is still lockstep, so nothing about a rate can ever be read off the simulator.
- `--robot arm-01:mujoco` does not parse, because a registered name is a name and not an
  adapter.
- A bare `--robot lerobot:mujoco` would read the calibration under `arm-01`, the id quackd gives
  an arm nobody named, and so load the calibration of whatever arm this machine last calibrated
  under that name.
- The world's truth has to be read before the teardown moves anything, or every run is judged
  on where the rest move left the scene.
- A command that rehearses a task file seed after seed must refuse a real arm outright.
- CI must not fetch the model. A job a pull request waits on would fail whenever upstream did,
  and no CI fixture carries a byte of an upstream asset.
- A released arm on a simulator never falls unless something lets time pass, because nobody
  is there to place it.

Two faults already in the code were found along the way, and each has its own commit and its
own note. `lerobot:real` built its follower asking LeRobot's garbage-collection disconnect to
drop torque, so an exit that skipped `close()` could drop the arm
([ADR-0036](0036-what-the-arm-does-not-say.md)). And `FlockClock` could lose a waiter when two
tasks slept under one id ([ADR-0016](0016-flock-lockstep-clock.md)).

What happened at the bench is the reason for this decision and nothing more. No number from
that afternoon's traces is a constant anywhere below: travel comes from a calibration file read
at connect, units, ranges and camera mounts from the loaded model, the stand-in arm's ranges
from the manifest's joint limit, and motor ids from LeRobot's own bus table.

## Decision

**`lerobot:mujoco` is the real backend with a MuJoCo follower underneath.** `LeRobotSim` in
`sim/transport.py` subclasses `LeRobotReal` and changes what sits under it, not what runs in
it. Its two builders return a `SimFollower` where the real backend's return LeRobot's
follower, and a `SimCamera` per camera where the real backend's open a webcam, and its clock is
the world's. Every line in between is the real backend's own: the connect and its retries, the
travel read off the calibration and the refusals past it, the rest move clipped into the
travel, the hold that skips a joint past it, the release and the take-hold, the close and what
it says, and the policy loop. A refusal names its backend through a label, `lerobot mujoco:`
where the arm's says `lerobot real:`, and the camera parser takes the same label with a hint
about the scene's mounts. The simulator is known to be one before anything is built:
`quackd_lerobot` exports `SIMULATOR_BACKENDS = ("mujoco",)`, and the core asks
`factory.is_simulator(spec)`, which is how `preflight` refuses a real arm and how `doctor`
knows it may connect a registered simulator. Once one is built, the adapter's own
`is_simulator` is how `doctor` and `robot release` know not to ask anybody to hold it. Neither
is `perception.is_simulated`, which counts a mock too.

**The follower does what LeRobot does with a goal, and then what the Feetech servo does with
it.** `SimFollower` carries exactly the surface `real.py` touches, which the tests' `FakeArm`
already spelled out, and under it:

- each send is capped at `max_relative_target` from the present reading, by a copy of LeRobot's
  `ensure_safe_goal_position` with its two errors, cited as `ENSURE_SAFE_GOAL_POSITION` rather
  than imported, so the simulator needs neither Python 3.12 nor torch;
- the goal is then clamped to the calibrated travel without a word, as the servo clamps it to
  the position limits calibration wrote (`POSITION_LIMITS_CLAMP_GOALS`), and a reading is never
  clamped, so a joint can read past its travel as it does on the arm;
- a limp joint keeps the goal it was last written and drives to it when torque returns. That is
  the worst case of `TORQUE_ENABLE_HOLDS_PRESENT`, which stays UNVERIFIED, and the simulator
  plays the worst case so that a rehearsal meets it;
- positions are whole encoder ticks through the calibration, as LeRobot converts them, and the
  gripper is LeRobot's 0 to 100;
- a follower collected while connected disconnects as `Robot.__del__` does (`ROBOT_DEL`), with
  whatever torque flag its config holds by then;
- its faults are raised from functions named as upstream's, in upstream's words, so
  `may_have_written_torque` and `motor_in_error` sort them as they sort the arm's.

`tests/test_lerobot_sim_parity.py` runs the real backend over this follower and over `FakeArm`
side by side, and the one place they differ by design, the goal a limp joint keeps, is run over
the simulator alone.

**The model is the maker's, fetched at a pin and never shipped.** TheRobotStudio's SO-ARM100 at
`5f6d2b8`, `Simulation/SO101/so101_new_calib_camera.xml`: the new_calib arm with upstream's
wrist camera mount. Its joint and actuator names are LeRobot's motor names, and new_calib is
the calibration LeRobot recommends. The file and the 15 meshes it names are fetched one at a
time from raw.githubusercontent, each checked against a recorded sha256, and installed into the
cache in one rename. The file defines no table, light, camera or physics option, so those are
quackd's: a scene built in Python around the model, with front, top and wrist views, contact
settings proved by a seeded grasp sweep, and gripper pads cut from the finger meshes' own
vertices at load time, because MuJoCo collides a mesh as its convex hull and the fixed finger's
hull fills the opening the jaw closes into. The map from LeRobot's degrees onto the model is a
zero offset and a positive sign on the five arm joints, as LeRobot's own kinematics helper puts
a reading on a model (`KINEMATICS_DEG2RAD`). Both are UNVERIFIED (`JOINT_ZERO`, `JOINT_SIGN`),
and so is the gripper's linear 0 to 100 over the model's hinge, whose closed end is found from
the model rather than assumed (`GRIPPER_MAP`).

**The travel is the arm's own, or a generic arm that says so.** A registered name, or
`--address` naming a file, reads the calibration LeRobot would read for that arm, through the
same search LeRobot walks, reimplemented without importing it, and decoded field by field as
LeRobot decodes it. That travel is the servo's clamp, and the model's own ranges are the hard
stops. A bare `--robot lerobot:mujoco` names no arm, so it gets the generic arm, whose
travel is the model's own range on every joint, and a connect note saying it is not any arm's
calibration. It never falls back to that default id. An address shaped like a serial port is
refused on its shape before anything looks at it, and a calibration is read only from a regular
file, because on Windows `COM5` is the port itself in whatever directory it is looked for. The
arm starts at its registered rest pose as read, limited by the model's stops, with a note for
any joint a stop truncates, and settled clear of the table and of itself where the pose puts it
into either (below).

**A start inside the table or inside the arm is settled out of it, and so is the pose the close
parks it in, and the close drives back to where it settled, or it is refused where it cannot be
settled clear.** The first runs with a real pilot, on 2026-09-28, found the lab arm's fold
putting the model's fingers into the table and its lower arm into its shoulder, under the
assumed joint zeros and signs. The first physics step threw the arm out, the elbow's actuator
ran out of force pushing back at a goal inside the table, and every run that let sim time pass
closed with the rest move stalled and torque on, while the scripted pilot, which lets no time
pass, found the arm already at rest. So when the world is built it reads every contact the arm
makes with the table and with its own links, and where any goes deeper than `START_CLEAR_M`, a
millimetre, the physics steps for `START_SETTLE_S` of sim time before the clock starts, with
every goal held and the objects out of the scene. Each joint that moved by more than an encoder
tick takes the angle it came to rest at, within the model's stops, as its start, its goal
register and, on the simulator only, the pose its close judges the rest move against, clipped
into the travel as any recorded pose is (`LeRobotSim._rest_target`), the way the real backend
parks a joint recorded past its travel at the edge of it. That parked pose is judged too. The
lab arm's twin started clear after the settle and still closed short of rest after a move of
shoulder_lift: the close parked shoulder_lift at the edge of its travel with the other joints
where the start settled them, and that pose put the gripper into the table. So the world is
given the travel the calibration recorded, and where the start with each joint past its travel
at the edge of it (`verbs.reachable_rest_goal`) goes deeper than `START_CLEAR_M`, that pose is
settled too, on a copy of the state, with the edge joints driven from the fold to the edge at
the rest move's pace, so the arm meets the table as a rest move does: posed at the edge and
settled from inside the table, a deep fold was thrown clear across its travel. Each other joint
takes the angle it came to rest at, within its travel and the model's stops, as its start as
well as its goal and rest, so one pose is both where the arm starts and where the close parks
it, and the edge joints keep their start and the half-line rule, which counts one the table
stops short of the edge, on the side of its fold, at rest where it stops. A connect note in the
words of the stop's note names the contacts, how deep each was and the joints that moved further
than a reached pose may miss by, and says the model's frame is an assumption until the bench
checks it. A start still in by more than `START_CLEAR_M` after the settle is refused at connect,
naming what the pose put where and what the settle left, and so is a parked pose no close could
call at rest, an edge joint pushed back into its travel further than a reached pose may miss by
or a start the adopted angles put back in, and the person is asked for a rest pose the model can
start at. A part held in against a stop, or pinned by a joint that cannot move, stays in however
long it settles, and settling again with every goal where the arm came to rest moves it no
further: an arm started there is jammed, and its first move stalls. Such folds exist among the
model's own stops, while the lab arm's fold settles clear. Settling was chosen over searching
for the nearest pose with no contact because the physics already is that search, along the
directions the contacts push, and it leaves the arm resting where gravity and the servos hold it
rather than at a pose that merely touches nothing. A search along some other path for a start
the settle cannot clear was rejected as well: it would start the arm at a pose neither the arm
nor the person chose, and a twin is for rehearsing the pose its arm really rests in. Lowering
the table would change every scene to hide one pose. A looser stall check or rest tolerance
would pass a close that misses a pose on the arm as well, and the real backend's own judgement
of its rest move is not changed at all. The settle judges the start and the parked pose and not
the way back between them: after a move that takes the arm off its fold, whether it lifts the
arm upright, moves `shoulder_lift` within its travel or drags the gripper across the table, the
rest move can set the gripper down on the table short of its settled angles, where the table
holds the wrist or the elbow further from its goal than a reached pose may miss by, and the
close then reports the stall and keeps torque on. Every such stall measured had the arm
touching the table and nothing else. Parking with some clearance above the table freed some of
those in a trial and was not taken, because nothing measured says how much clearance an arm
keeps. Where the fold and the model disagree is left to the bench, and so is whether the arm's
own rest move presses its gripper into the table, which the model's did with the recorded fold
at the edge of the lab arm's travel before the parked pose was settled.

**Time is lockstep, with a participant per sleep.** `SimClock` sits on `FlockClock` and uses
only its public calls. Every `sleep(s)` takes a fresh id, numbered in arrival order and
zero-padded so the flock clock's sorted wake order is arrival order, parks under it and
unregisters it on the way out. So time runs while every sleeper is parked and stops the moment
any of them is awake, and with nobody sleeping nobody is registered and time stands still. A
pilot's thinking, a verb's reads and writes and a lone task's work between two sleeps cost no
sim time, and two verbs sleeping at once over MCP both reach their wake-up. A sleep of zero or
less yields to the loop and never touches the flock clock. The clock turns the flock clock's
interruptions into the transport's own errors, so the rest move and the take-hold, which sleep
on it directly, meet the same failures a verb does. Its step is the model's timestep times five
substeps, 0.01 s, a tenth of a verb's tick. A sleep is rounded to whole steps, so the clock
refuses to be built on a step that does not divide the verbs' own tick and poll, which would
drift against their arithmetic. `--live` paces the same clock on `perf_counter` and opens
MuJoCo's viewer, and it is still lockstep. A run through the agent loop, and a rehearsal, makes
one call at a time, so under one seed the simulator does the same again. An MCP session runs
tool calls concurrently, and it is not seeded.

**Every GL call is on the event loop's thread.** Making a renderer, rendering and freeing one
all happen there, because a GL context belongs to the thread that made it, and that is the one
path proven on Windows. The real backend reads a camera from a worker thread, so a read hops to
the loop to render and waits for its frame. A camera renders only when its frame is older than
the world, which it cannot be while nobody sleeps, so two reads between two sleeps cost one
render. Physics steps under one lock around the model's data, and worker threads only make short
reads and control writes under it.

**Faults are seeded and counted per kind, and the heartbeat draws none.** A spec of rates for the
handshake, `configure()`'s Lock write, a goal write, a torque write and the torque and
temperature reads, plus `read_loss_from=N` for an arm that drops off the bus, is given with
`--faults` to `quackd preflight`, and to `make()` only when given. The call at ordinal `n` of
kind `k` fails when a draw keyed on the seed, `k` and `n` falls under `k`'s rate, so a fault lands
on the same call of its kind whatever else happened, and a retry is an ordinal of its own. The
heartbeat reads on the wall's clock, so how many reads it has made by any given call is chance,
and it takes no ordinal and draws no rate. It is not exempt from the arm dropping off the bus,
because a pulled cable fails every read at the lab. A transient failure of one observation stays
in `FakeArm`'s unit tests.

**The world's truth never reaches the pilot, the stepper or MCP.** Where every object is, now
and at its peak, and whether both fingers are on it, lives on the transport's `sim_world`, and
never in the state's extras, which `report_state` hands a pilot and an MCP client. The
attribute is not called `world`, which the core would take for a world to record a GIF of.
`stop()` and `go_to_rest()` latch the truth on their way in, before the teardown's rest move
carries the arm back through the scene, and what a rehearsal judges is that snapshot or the peak
before it. The heartbeat's reads no longer feed the grasp trace `pick` watches, so a grasp is
judged on the loop's own reads. On an arm, `pick` can notice a grasp one poll later than it did,
and nothing in quackd loads a policy for `pick` yet.

**Nobody places a released arm, so gravity does.** `take_hold()` first lets the released arm
fall for `PLACE_SETTLE_S` of sim time, and then meets whatever pose the table, the stops and its
own weight have left, which rehearses the take-hold's refusals for a slip or a joint past its
travel ([ADR-0039](0039-an-arm-placed-by-hand.md), [ADR-0045](0045-a-rest-pose-the-calibration-cannot-reach.md))
as they happen. A close over an arm still in a hand lets it settle the same way. Closing the
`--live` viewer during that fall is a stop.

**`quackd robot twin` makes a simulator of a registered arm.** `twin SOURCE [NAME]` registers
NAME, `SOURCE-sim` unless named, as `lerobot:mujoco`, with the calibration file SOURCE's runs
read as its address, and SOURCE's rest pose, pilot and each camera the simulator renders.
Memory stays separate under NAME. It refuses a NAME that is SOURCE, a source that is not a
registered LeRobot arm, a missing calibration file, a port where the file goes, and `--force`
over anything that is not already `lerobot:mujoco`, so no arm is overwritten by its own
simulator. A backend on a registered name, `--robot arm-01:mujoco`, would have been a second
grammar for `--robot` to save one command.

**`quackd preflight` rehearses task files on a simulator, and refuses anything else before it is
built.** A robot for which `is_simulator` is False is refused with a pointer to `robot twin`,
before the factory makes anything, because a real arm refused after it was built would already
have had its port opened. So is a pilot nobody named, since the scripted one rehearses nothing a
model would do and runs only when `--llm fake` is typed. Each file is validated against the
simulator's manifest, connected and closed a few times with the fault plan, then run once per
seed through the agent loop with memory off, in a loop of preflight's own that keeps the
transport, because what a run is judged by is on it. A run passes when nothing escaped, no bus
call was left hanging, the close ended at the rest pose, found none to return to on a robot
without one, or had its rest move refused where the task expects a refusal (`at_rest: false`),
and every check in the task's sidecar held. A rest move that stalled or ran out of time fails a
run whatever the sidecar says, and the close is named for which it was. A sidecar that asks
`at_rest: true` fails a robot with no rest pose, and one that asks `at_rest: false` passes it,
since no rest move was made. The sidecar is
`<task>.sim.yaml` beside the file and never its frontmatter, which `robot_load_duckfile` hands
an MCP pilot whole: a pilot that can read what it will be marked on is rehearsing the marking.
It lays out the table and says what has to be so, `at_rest`, `joint_moved`, `lifted` and
`moved`, with every threshold the task's own and measured from the run's start.

**CI runs a stand-in, and the real model runs nightly.** `sim/standin.py` is a primitives-only
arm with LeRobot's six joint names, its ranges from `limits.joint_deg` in the manifest, which
needs nothing fetched. CI's `physics` job runs the parity, clock, fault, camera and preflight
tests on it with MuJoCo rendering through OSMesa. The tests marked `so101_model` need the maker's
model and skip without it, and `lerobot-sim-assets.yml` fetches it the way a first run does,
into a runner that is then destroyed, and runs them asking ten seeds of ten. That job is also
the watchdog on the pin, because a file that stops arriving at its hash fails there, by name.
GitHub runs a scheduled workflow only from the default branch, so the job's first run is after
this reaches `main`. `lerobot:mujoco`'s ✅ rests on those sweeps, run by hand on 2026-09-27, and
it never raises `lerobot:real`'s status.

## Why not

**MuJoCo Menagerie's SO-101.** SO-ARM100 is the maker's own model, its joint names are LeRobot's
motor names, and LeRobot recommends the new_calib arm it describes. When the plan was checked,
Menagerie's `wrist_roll` joint range and its actuator's control range also disagreed with each
other.

**A free-running clock.** Time would pass while the pilot thinks, so a slow model and a fast one
would meet different worlds, a busy laptop would change the physics, and no seed would repeat a
run. Lockstep costs nothing an arm rehearsal needs: a position-controlled arm holds its goal
while nobody writes to it, and what a rate does on the real bus is a question only the bench
answers anyway.

**LeRobot's own simulation environments.** They are Gymnasium environments for training and
evaluating policies, which a policy steps, and not the `Robot` interface the real backend
drives, so a task rehearsed in one would run through code the lab never runs. They also need
LeRobot itself, which is Python 3.12 and torch, where this simulator installs on 3.11 with
MuJoCo alone.

**Isaac Lab.** It needs an NVIDIA RTX GPU and Isaac Sim underneath it. quackd's own promise is a
laptop with no GPU, and the laptop this project is built on has integrated graphics. What a
rehearsal needs is a follower that behaves like the servo under the backend's own code, and
MuJoCo on the CPU is enough for that.

## Consequences

- **What the simulator reproduces is what LeRobot and the servo do with a goal, under quackd's
  own code, and not the arm.** Its dynamics are the model's, a calculation and another robot's
  servo properties (`SERVO_DYNAMICS`), so a settle time, a push or a grasp that holds is
  evidence about the model. Only the bench can say it about an arm, and the simulator never
  raises `lerobot:real`'s status.
- **Only the bench can settle these, and PLAN.md carries them as open items.**
  - Joint signs and zero offsets: nudge each real joint by a small positive angle and compare
    the direction it moves in the simulator, and read the calibrated value at each mechanical
    stop against the model's stop, which also says whether a recorded fold can be represented at
    all. Until then a fold past a model stop is truncated at connect, and one that puts the model
    into its table or into itself is settled out of them, each with a note, or refused where
    settling cannot clear it.
  - The gripper on a real pen: its reading against the band that infers holding. The simulator's
    pen is a capsule in the model's physics and says nothing about that band.
  - The front and wrist cameras' extrinsics and field of view, which would replace the default
    mounts. Front and top are quackd's views of the table and not where any real camera stands,
    and the wrist view is rendered from upstream's printed mount, which may not be where the
    lab's camera sits (`WRIST_CAMERA_POSE`). A connect note and the state's assumptions say so.
  - The policy loop's rate on the real bus, which no lockstep clock can measure.
  - The seven bench steps 0.14.0 still owes, which the simulator does not replace.
- **The servos never warm.** Every temperature reads as a room's, so the heat refusal is never
  rehearsed.
- **A registry with a `lerobot:mujoco` robot in it is unreadable to quackd 0.14 and earlier,**
  which check every robot against the backends they know. `robot twin` says so in its warning
  and names every such robot in the file, and removing them makes the file readable again.
- **Physics is not the cost.** On this project's laptop the model steps several times faster
  than the wall with nothing rendering. What a rehearsal spends is its connects, each of which
  loads the model and renders once to prove the machine can draw, and its cameras, each of which
  renders again whenever it is read after the world has moved. There, on integrated graphics,
  two cameras about doubled the wall time of a rehearsal of `lerobot-lookout`.
- **A rehearsal takes one rest pose for all its seeds.** A seed lays the table out differently,
  but an object placed between the jaws sits where the arm's rest pose puts them, on every seed.
- [ADR-0030](0030-mujoco-physics-backend.md), [ADR-0016](0016-flock-lockstep-clock.md),
  [ADR-0036](0036-what-the-arm-does-not-say.md) and
  [ADR-0045](0045-a-rest-pose-the-calibration-cannot-reach.md) each carry a note pointing here.
  None of their decisions is reversed.
