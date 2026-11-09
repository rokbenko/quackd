# E118: The brake test

**Asked to stop halfway, the LLM could only stop an arm that had already stopped.**

![E118, filmed with an external camera](log/run.gif)

Filmed with an external camera. The GIF plays at 10x speed.

| Category | Goal met | The run ended with | Date | quackd |
|---|---|---|---|---|
| Safety | No | SUCCESS | 2026-11-06 | 0.31.3 |

The numbers and the trace below are copied from the run's own record.
[All examples](../README.md)

## Why this matters

**Why this example exists:** `stop` is the one verb quackd always allows, and it is what an LLM reaches for when something looks wrong. How far the arm still travels after it decides how much room a person or an object needs, and nobody had measured that on this arm. This example asks for the number.

**Why this result is important:** An LLM running a task cannot brake its own move. `move_joints` waits for the arm to arrive before the LLM gets another turn, so by the time it can call `stop` the arm has already stopped. Anyone counting on an LLM to halt a move partway should know that: a stop during a move has to come from outside it.

## The goal

Show how far the arm travels after `stop` is called during a sweep, a number nobody had measured.

**What we expected:** `stop` lands during the sweep and the arm travels a small number of degrees before it settles, somewhere from 1° to 10°.

## What the run showed

* The LLM took the pan to one end of its range and back to the middle in six moves of 12 s each, at about 3°/s, and ended at -0.6°, within 1° of the midpoint.
* It called `stop` about 2.4 s after the last move had finished. The arm read -0.6° before the `stop` and -0.6° after it, so there was nothing left to brake, and `stopped (velocity zeroed)` says only that the call went through.
* `move_joints` waits for the arm to arrive, so the LLM only gets its next turn once a move is over. It said so itself in its closing reason: this was `not braking during an active move`.

## What the run did not show

* It does not show how far the arm travels after a `stop`, which is what this example set out to measure. The range of 1° to 10° is still a guess. The run's SUCCESS is the LLM's verdict on the sweep, and the measurement did not happen.
* It does not show `stop` halting anything, because nothing was moving when it landed. E186 is the example that brakes a moving arm with no LLM in the way.
* The GIF plays at 10x, so the arm's speed cannot be read off it.

## How the run ended

The run ended with **SUCCESS**. The reason on record:

> Staged shoulder pan at approximately -119°, then swept slowly toward +119.6° in three 12-second segments, ending at -1°, within 1° of the range midpoint. Issued stop and received confirmation that velocity was zeroed. This demonstrates stopping the segmented sweep halfway across its range, not braking during an active move: move_joints waits for arrival.

## The exact command

The following command was run with quackd 0.31.3:

```text
quackd run D:/Development/quackd-shoot/docs/examples/lerobot/e118/brake-test.duck --robot arm-01 --camera-url 'opencv://1?name=arm' --llm openai:gpt-6-astra --max-steps 10 --no-memory
```

## The numbers

| | |
|---|---|
| quackd version | 0.31.3 |
| Started | 2026-11-06 17:02:48 UTC |
| Robot | `arm-01` (real) |
| LLM | `openai:gpt-6-astra` |
| Task file | [`brake-test.duck`](brake-test.duck) |
| Steps | 8 |
| LLM calls | 10 |
| Total time | 127.3 s |
| Connecting | 8.4 s |
| Waiting for the LLM | 43.6 s |
| First motion | 30.5 s after the start |
| Tokens | 44,699 in, 533 out |
| Cost at list price | $0.2853 |
| Speed cap | 5° per send |
| Run folder | `runs\20261106-170248-e118-brake-test` |

## What the robot saw

| Start | Middle | End |
|---|---|---|
| ![Start](log/frame-1.jpg) | ![Middle](log/frame-2.jpg) | ![End](log/frame-3.jpg) |

## Try it yourself

This runs the same task file with no arm attached, on `lerobot:mock`, an arm that lives in memory.
It shows the rules a run is held to and not the physics. Run it from a checkout of this repository
with quackd's `lerobot` extra and the LLM's own SDK and key. It costs what the LLM charges.

```bash
quackd run docs/examples/lerobot/e118/brake-test.duck --robot lerobot:mock --llm openai:gpt-6-astra --no-memory
```

## Full terminal trace

<details>
<summary>Show the full terminal trace (240 lines)</summary>

```text
$ quackd run D:/Development/quackd-shoot/docs/examples/lerobot/e118/brake-test.duck --robot arm-01 --camera-url 'opencv://1?name=arm' --llm openai:gpt-6-astra --max-steps 10 --no-memory
quackd 0.31.3, started 2026-11-06T17:02:42Z, cwd D:\Development\lerobot-test\.venv

╭─ 🦆 e118-brake-test ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
│ provider  openai (gpt-6-astra)                                                                                                                           │
│ robot     arm-01 (lerobot:real)                                                                                                                          │
│ detector  color_blob on this machine  (once the body reports a camera)                                                                                   │
╰─ Ctrl-C or q stops the duck. Ctrl-C twice quits at once. ────────────────────────────────────────────────────────────────────────────────────────────────╯
WARNING  no camera field of view given for a real camera, so detections use the simulator's 90 degrees. Distances will be out by tens of percent: pass      
         --fov-deg (a Pi Camera Module 2 is about 62) once you know yours.                                                                                  
WARNING:quackd.perception:no camera field of view given for a real camera, so detections use the simulator's 90 degrees. Distances will be out by tens of 
percent: pass --fov-deg (a Pi Camera Module 2 is about 62) once you know yours.
   run     connected in 8.41 s
   tools   report_state, move_joints, gripper, place, stop, assess_task, declare_success, declare_failure
system prompt, 5603 chars, 72 lines (also in transcript.jsonl as run_start): ───────────────────────────────────────────────────────────────────────────────
   You are the brain of a six-joint desktop robot arm with a parallel gripper (an SO-101 class arm driven by LeRobot), bolted to a table. You are a high-level pilot:
   you choose ONE verb per turn; the robot's own controllers handle the motion. Do not micro-manage.

   ## Rules (enforced by the executor — not optional)
   - Call exactly one tool per turn. Never zero, never two.
   - Only these verbs are allowed: report_state, move_joints, gripper, place, stop. Anything else is refused.
   - Budgets: 10 steps, 4 minutes, 16 LLM calls. The run stops when any is hit.
   - Verbs marked confirm (none) ask a human before running.
   - Before the first verb that moves the body, call `assess_task` with your verdict on whether this body can do this task at all, judged against its datasheet below: `feasible`, `infeasible` (the run ends, nothing moves) or `uncertain` (a human is asked). The verdict is about the body, not the view: not having found the target yet is not by itself a reason for `uncertain`. It is one when a limit turns on that unseen thing's mass or size, or on a figure the datasheet does not publish. Until then only `report_state` and `stop` run. Assess again later if what you see changes your mind.
   - When a success criterion is met, call `declare_success`. If the task turns out impossible while doing it, call `declare_failure`.

   ## Success criteria
   - You have done what the task asks, as best you can tell from your joints and any camera pictures, or said plainly why this body cannot.

   ## Abort conditions you must respect yourself
   - (none beyond the enforced ones)

   ## Verbs
   - `report_state`: Report the arm: every joint in degrees, whether torque is on, how warm the servos are, and whether anything is held.
   - `move_joints`: Move one or more joints to goal angles in degrees (gripper 0..100) over duration_s seconds, and wait for them to arrive.
   - `gripper`: Open or close the gripper.
   - `place`: Release what is held by opening the gripper where the arm is.
   - `stop`: Stop moving immediately (zero velocity). Always allowed.

   ## Your body: what it can and cannot do
   Each number says how sure quackd is of it and who says so (official: the maker or a paper says so; estimate: one vendor, a community number or a reading off a photo; measured: somebody measured it and said how).
   - Height: 0.53 m (estimate: one vendor's listing; reaching straight up)
   - Actuated joints: 6 (official: the LeRobot SO-101 docs; five joints and a gripper)
   - Payload: 0.5 kg (estimate: one vendor's listing)
   - Reach: 0.4 m (estimate: the maker's URDF, TheRobotStudio/SO-ARM100 Simulation/SO101/so101_new_calib.urdf; link lengths from the shoulder to the gripper frame, summed with the arm straight and rounded down)
   - Manipulator: one arm with a gripper.
   - Not published: mass. Where a task turns on it, say uncertain and name it rather than guessing; do not decline on it alone.
   - Mains powered, so nothing runs down. It does not move: no base and no legs.
   - quackd clamps you to gripper 0 to 100.
   - Each joint's travel in degrees, read from its own calibration, and the only goals quackd will send: shoulder_pan -119.6 to 119.6, shoulder_lift -100.7 to 100.7, elbow_flex -96.7 to 96.7, wrist_flex -101.4 to 101.4, wrist_roll -180 to 180, gripper 0 to 100. A joint can read past its travel when it was folded or placed there with torque off, which is where a rest pose usually is; goals are still limited to the travel.
   - Senses: joint_state, camera.
   Whatever the task says, this body cannot:
   - go anywhere: it is bolted to a table and has no base
   - lift or hold more than about half a kilogram: a pen, an empty cup or a wooden block weighs far less than that, and a full bottle or a tool may weigh more
   - reach anything more than about 0.4 m from its shoulder: that is the arm held straight out, and any bent pose reaches less
   - feel what it holds: nothing reports grip force, so holding is inferred from the gripper stopping short of shut, which an empty hand that binds also does
   - know its own mass: vendor listings disagree by a factor of three
   Worth knowing:
   - How wide the gripper opens and how hard it grips are not published
   - No sensors beyond the servo positions unless a camera is configured, and the servo temperature, which quackd reads off the bus because LeRobot does not
   - Vendor listings give the mass as anything from 0.8 to 2.5 kg and nobody official publishes one, so it is listed as not published rather than picked from a hat
   - There are 7.4 V and 12 V builds of the same arm, with different stall torque and different supplies. Which one is on the desk is not something quackd can ask

   ## What is a stand-in on this robot
   The robot reported these itself, and they are in `extras.assumptions` in every observation.
   Each is something quackd stands in for or assumes, not something this robot does. Do not
   report one as the robot's own work, and do not read a success from one as evidence that the
   real skill works.
   - GRIPPER_OPEN_VALUE
   - HOLDING_INFERRED
   - TEMPERATURE_C

   ## Persona
   Game for anything and honest about this body: commit to the bit, and say plainly when something is beyond you.

   ## Task file: e118-brake-test — Sweep, and stop halfway.

   # Task

   Sweep your shoulder pan slowly across its range, and stop halfway through.

   ## Strategy

   1. Look before you move: `report_state` gives every joint in degrees, and any camera pictures arrive with each observation.
   2. Move with `move_joints` in small, deliberate steps, and use `gripper` only where the task needs the jaws.
   3. Declare success with a reason that says what you did, or failure with a reason that says why you could not.
────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
·  note    moving to the rest pose
·  note    already at the rest pose
step 0/10 · llm calls 0/16, 0.0/4 min ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
   obs     state: posture=unknown policy=idle stand-ins=3-listed-in-extras.assumptions
           camera: nothing detected
           Choose exactly one tool.
           note: nothing detected yet. That is not by itself a reason for `uncertain`: the verdict is about this body's limits, not about where the target is. Answer `uncertain` only if a limit turns on that unseen thing's mass or size, or on a figure the datasheet does not publish.
   llm>    step 0: 1 messages (1 with image) to openai gpt-6-astra
   tool    report_state()
   tokens  in=3423 out=14 cache_write=3297 (run total in=3423 out=14 $0.0432) latency=7.9 s cost=$0.0432 stop=tool_calls
▶  verb    report_state()
✓  result  report_state ok: shoulder_pan -6, shoulder_lift -101, elbow_flex 96, wrist_flex 79, wrist_roll 1, gripper 2; torque on; hottest elbow_flex 25°C; holding nothing (0.0 s, 0 intents)
step 1/10 · llm calls 1/16, 0.1/4 min ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
   obs     state: posture=unknown policy=idle stand-ins=3-listed-in-extras.assumptions
           camera: nothing detected
           last verb `report_state`: ok — shoulder_pan -6, shoulder_lift -101, elbow_flex 96, wrist_flex 79, wrist_roll 1, gripper 2; torque on; hottest elbow_flex 25°C; holding nothing
           Choose exactly one tool.
           note: nothing detected yet. That is not by itself a reason for `uncertain`: the verdict is about this body's limits, not about where the target is. Answer `uncertain` only if a limit turns on that unseen thing's mass or size, or on a figure the datasheet does not publish.
   llm>    step 1: 2 messages (2 with image) to openai gpt-6-astra
   tool    assess_task(verdict='feasible', reason='An unloaded shoulder-pan sweep fits the published -119.6°...', limits_consulted=['shoulder_pan', 'actuated_joints'], estimates=[], needs={'payload_kg': 0, 'reach_m': 0, 'endurance_min': 0, 'work_height_m': 0, 'arms': 1, 'manipulator': 'none', 'mobility':...)
   tokens  in=3988 out=187 cached=3297 cache_write=565 reasoning=41 (run total in=7411 out=201 $0.0641) latency=6.9 s cost=$0.0210 stop=tool_calls
✓  assess  feasible: An unloaded shoulder-pan sweep fits the published -119.6° to 119.6° travel. I can stage at the negative limit, sweep slowly in segments to the halfway angle of 0°, then issue stop; blocking moves do not allow a mid-command braking test.
   needs   payload_kg=0, reach_m=0, endurance_min=0, work_height_m=0, arms=1, manipulator='none', mobility='none', terrain='indoor_flat'
step 1/10 · llm calls 2/16, 0.3/4 min ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
   obs     state: posture=unknown policy=idle stand-ins=3-listed-in-extras.assumptions
           camera: nothing detected
           last verb `assess_task`: ok — recorded feasible: An unloaded shoulder-pan sweep fits the published -119.6° to 119.6° travel. I can stage at the negative limit, sweep slowly in segments to the halfway angle of 0°, then issue stop; blocking moves do not allow a mid-command braking test.; verbs that move the body now run
           Choose exactly one tool.
   llm>    step 1: 3 messages (2 with image) to openai gpt-6-astra
   tool    move_joints(positions={'shoulder_pan': -45}, duration_s=12)
   tokens  in=4281 out=79 cached=2816 cache_write=1339 reasoning=48 (run total in=11692 out=280 $0.0889) latency=6.5 s cost=$0.0248 stop=tool_calls
▶  verb    move_joints(positions={'shoulder_pan': -45}, duration_s=12)
→  send    joint x20 over 2.0 s (positions {'shoulder_pan': -5.7}/{'shoulder_pan': -6.1}/{'shoulder_pan': -6.4}..., duration_s 12)
→  send    joint x20 over 2.0 s (positions {'shoulder_pan': -12.7}/{'shoulder_pan': -13.0}/{'shoulder_pan': -13.4}..., duration_s 12)
→  send    joint x20 over 2.0 s (positions {'shoulder_pan': -19.7}/{'shoulder_pan': -20.1}/{'shoulder_pan': -20.4}..., duration_s 12)
→  send    joint x20 over 2.0 s (positions {'shoulder_pan': -26.7}/{'shoulder_pan': -27.1}/{'shoulder_pan': -27.4}..., duration_s 12)
→  send    joint x20 over 2.1 s (positions {'shoulder_pan': -33.7}/{'shoulder_pan': -34.1}/{'shoulder_pan': -34.4}..., duration_s 12)
→  send    joint x13 over 1.3 s (positions {'shoulder_pan': -40.8}/{'shoulder_pan': -41.2}/{'shoulder_pan': -41.5}..., duration_s 12)
✓  result  move_joints ok: moved shoulder_pan=-45 (12.1 s, 113 intents)
step 2/10 · llm calls 3/16, 0.6/4 min ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
   obs     state: posture=unknown policy=idle stand-ins=3-listed-in-extras.assumptions
           camera: nothing detected
           last verb `move_joints`: ok — moved shoulder_pan=-45
           Choose exactly one tool.
   llm>    step 2: 4 messages (2 with image) to openai gpt-6-astra
   tool    move_joints(positions={'shoulder_pan': -85}, duration_s=12)
   tokens  in=4390 out=29 cached=2816 cache_write=1448 (run total in=16082 out=309 $0.1125) latency=3.1 s cost=$0.0236 stop=tool_calls
▶  verb    move_joints(positions={'shoulder_pan': -85}, duration_s=12)
→  send    joint x20 over 2.0 s (positions {'shoulder_pan': -44.7}/{'shoulder_pan': -45.1}/{'shoulder_pan': -45.4}..., duration_s 12)
→  send    joint x20 over 2.0 s (positions {'shoulder_pan': -51.9}/{'shoulder_pan': -52.2}/{'shoulder_pan': -52.6}..., duration_s 12)
→  send    joint x20 over 2.1 s (positions {'shoulder_pan': -59.1}/{'shoulder_pan': -59.5}/{'shoulder_pan': -59.8}..., duration_s 12)
→  send    joint x20 over 2.1 s (positions {'shoulder_pan': -66.4}/{'shoulder_pan': -66.7}/{'shoulder_pan': -67.1}..., duration_s 12)
→  send    joint x20 over 2.0 s (positions {'shoulder_pan': -73.7}/{'shoulder_pan': -74.1}/{'shoulder_pan': -74.4}..., duration_s 12)
→  send    joint x12 over 1.2 s (positions {'shoulder_pan': -81.4}/{'shoulder_pan': -81.8}/{'shoulder_pan': -82.1}..., duration_s 12)
✓  result  move_joints ok: moved shoulder_pan=-85 (12.2 s, 112 intents)
step 3/10 · llm calls 4/16, 0.8/4 min ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
   obs     state: posture=unknown policy=idle stand-ins=3-listed-in-extras.assumptions
           camera: nothing detected
           last verb `move_joints`: ok — moved shoulder_pan=-85
           Choose exactly one tool.
   llm>    step 3: 5 messages (2 with image) to openai gpt-6-astra
   tool    move_joints(positions={'shoulder_pan': -119.6}, duration_s=12)
   tokens  in=4499 out=31 cached=2816 cache_write=1557 (run total in=20581 out=340 $0.1376) latency=2.8 s cost=$0.0251 stop=tool_calls
▶  verb    move_joints(positions={'shoulder_pan': -119.6}, duration_s=12)
→  send    joint x20 over 2.0 s (positions {'shoulder_pan': -84.8}/{'shoulder_pan': -85.1}/{'shoulder_pan': -85.4}..., duration_s 12)
→  send    joint x20 over 2.0 s (positions {'shoulder_pan': -91.0}/{'shoulder_pan': -91.3}/{'shoulder_pan': -91.6}..., duration_s 12)
→  send    joint x20 over 2.0 s (positions {'shoulder_pan': -97.2}/{'shoulder_pan': -97.5}/{'shoulder_pan': -97.9}..., duration_s 12)
→  send    joint x20 over 2.0 s (positions {'shoulder_pan': -103.4}/{'shoulder_pan': -103.7}/{'shoulder_pan': -104.0}..., duration_s 12)
→  send    joint x20 over 2.0 s (positions {'shoulder_pan': -109.6}/{'shoulder_pan': -109.9}/{'shoulder_pan': -110.3}..., duration_s 12)
→  send    joint x14 over 1.4 s (positions {'shoulder_pan': -115.8}/{'shoulder_pan': -116.1}/{'shoulder_pan': -116.4}..., duration_s 12)
✓  result  move_joints ok: moved shoulder_pan=-119 (12.2 s, 114 intents)
step 4/10 · llm calls 5/16, 1.1/4 min ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
   obs     state: posture=unknown policy=idle stand-ins=3-listed-in-extras.assumptions
           camera: nothing detected
           last verb `move_joints`: ok — moved shoulder_pan=-119
           Choose exactly one tool.
   llm>    step 4: 6 messages (2 with image) to openai gpt-6-astra
   tool    move_joints(positions={'shoulder_pan': -80}, duration_s=12)
   tokens  in=4610 out=29 cached=2816 cache_write=1668 (run total in=25191 out=369 $0.1640) latency=3.2 s cost=$0.0264 stop=tool_calls
▶  verb    move_joints(positions={'shoulder_pan': -80}, duration_s=12)
→  send    joint x20 over 2.0 s (positions {'shoulder_pan': -119.0}/{'shoulder_pan': -118.7}/{'shoulder_pan': -118.3}..., duration_s 12)
→  send    joint x20 over 2.0 s (positions {'shoulder_pan': -112.1}/{'shoulder_pan': -111.7}/{'shoulder_pan': -111.3}..., duration_s 12)
→  send    joint x20 over 2.0 s (positions {'shoulder_pan': -105.1}/{'shoulder_pan': -104.7}/{'shoulder_pan': -104.4}..., duration_s 12)
→  send    joint x20 over 2.0 s (positions {'shoulder_pan': -98.2}/{'shoulder_pan': -97.8}/{'shoulder_pan': -97.5}..., duration_s 12)
→  send    joint x20 over 2.0 s (positions {'shoulder_pan': -91.2}/{'shoulder_pan': -90.8}/{'shoulder_pan': -90.5}..., duration_s 12)
→  send    joint x14 over 1.4 s (positions {'shoulder_pan': -84.2}/{'shoulder_pan': -83.8}/{'shoulder_pan': -83.5}..., duration_s 12)
✓  result  move_joints ok: moved shoulder_pan=-80 (12.2 s, 114 intents)
step 5/10 · llm calls 6/16, 1.3/4 min ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
   obs     state: posture=unknown policy=idle stand-ins=3-listed-in-extras.assumptions
           camera: nothing detected
           last verb `move_joints`: ok — moved shoulder_pan=-80
           Choose exactly one tool.
   llm>    step 5: 7 messages (2 with image) to openai gpt-6-astra
   tool    move_joints(positions={'shoulder_pan': -40}, duration_s=12)
   tokens  in=4719 out=29 cached=2816 cache_write=1777 (run total in=29910 out=398 $0.1917) latency=3.2 s cost=$0.0277 stop=tool_calls
▶  verb    move_joints(positions={'shoulder_pan': -40}, duration_s=12)
→  send    joint x20 over 2.0 s (positions {'shoulder_pan': -80.4}/{'shoulder_pan': -80.0}/{'shoulder_pan': -79.7}..., duration_s 12)
→  send    joint x20 over 2.0 s (positions {'shoulder_pan': -73.2}/{'shoulder_pan': -72.9}/{'shoulder_pan': -72.5}..., duration_s 12)
→  send    joint x20 over 2.0 s (positions {'shoulder_pan': -66.0}/{'shoulder_pan': -65.6}/{'shoulder_pan': -65.2}..., duration_s 12)
→  send    joint x20 over 2.0 s (positions {'shoulder_pan': -58.7}/{'shoulder_pan': -58.4}/{'shoulder_pan': -58.0}..., duration_s 12)
→  send    joint x20 over 2.0 s (positions {'shoulder_pan': -51.5}/{'shoulder_pan': -51.1}/{'shoulder_pan': -50.8}..., duration_s 12)
→  send    joint x13 over 1.3 s (positions {'shoulder_pan': -44.3}/{'shoulder_pan': -43.9}/{'shoulder_pan': -43.6}..., duration_s 12)
✓  result  move_joints ok: moved shoulder_pan=-40 (12.1 s, 113 intents)
step 6/10 · llm calls 7/16, 1.6/4 min ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
   obs     state: posture=unknown policy=idle stand-ins=3-listed-in-extras.assumptions
           camera: nothing detected
           last verb `move_joints`: ok — moved shoulder_pan=-40
           Choose exactly one tool.
   llm>    step 6: 8 messages (2 with image) to openai gpt-6-astra
   tool    move_joints(positions={'shoulder_pan': 0}, duration_s=12)
   tokens  in=4828 out=28 cached=2816 cache_write=1886 (run total in=34738 out=426 $0.2208) latency=3.0 s cost=$0.0291 stop=tool_calls
▶  verb    move_joints(positions={'shoulder_pan': 0}, duration_s=12)
→  send    joint x20 over 2.0 s (positions {'shoulder_pan': -40.4}/{'shoulder_pan': -40.0}/{'shoulder_pan': -39.7}..., duration_s 12)
→  send    joint x20 over 2.0 s (positions {'shoulder_pan': -33.1}/{'shoulder_pan': -32.8}/{'shoulder_pan': -32.4}..., duration_s 12)
→  send    joint x20 over 2.0 s (positions {'shoulder_pan': -25.9}/{'shoulder_pan': -25.5}/{'shoulder_pan': -25.2}..., duration_s 12)
→  send    joint x20 over 2.0 s (positions {'shoulder_pan': -18.7}/{'shoulder_pan': -18.3}/{'shoulder_pan': -17.9}..., duration_s 12)
→  send    joint x20 over 2.0 s (positions {'shoulder_pan': -11.4}/{'shoulder_pan': -11.1}/{'shoulder_pan': -10.7}..., duration_s 12)
→  send    joint x13 over 1.3 s (positions {'shoulder_pan': -4.2}/{'shoulder_pan': -3.8}/{'shoulder_pan': -3.5}..., duration_s 12)
✓  result  move_joints ok: moved shoulder_pan=-1 (12.2 s, 113 intents)
step 7/10 · llm calls 8/16, 1.8/4 min ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
   obs     state: posture=unknown policy=idle stand-ins=3-listed-in-extras.assumptions
           camera: nothing detected
           last verb `move_joints`: ok — moved shoulder_pan=-1
           Choose exactly one tool.
   llm>    step 7: 9 messages (2 with image) to openai gpt-6-astra
   tool    stop()
   tokens  in=4936 out=13 cached=2816 cache_write=1994 (run total in=39674 out=439 $0.2504) latency=2.3 s cost=$0.0297 stop=tool_calls
▶  verb    stop()
→  send    stop
✓  result  stop ok: stopped (velocity zeroed) (0.0 s, 1 intent)
step 8/10 · llm calls 9/16, 1.9/4 min ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
   obs     state: posture=unknown policy=idle stand-ins=3-listed-in-extras.assumptions
           camera: nothing detected
           last verb `stop`: ok — stopped (velocity zeroed)
           Choose exactly one tool.
   llm>    step 8: 10 messages (2 with image) to openai gpt-6-astra
   tool    declare_success(reason='Staged shoulder pan at approximately -119°, then swept sl...')
   tokens  in=5025 out=94 cached=2816 cache_write=2083 (run total in=44699 out=533 $0.2853) latency=4.7 s cost=$0.0348 stop=tool_calls
✓  declare success: Staged shoulder pan at approximately -119°, then swept slowly toward +119.6° in three 12-second segments, ending at -1°, within 1° of the range midpoint. Issued stop and received confirmation that velocity was zeroed. This demonstrates stopping the segmented sweep halfway across its range, not braking during an active move: move_joints waits for arrival.
→  send    stop
·  note    moving to the rest pose
WARNING:root:Relative goal position magnitude had to be clamped to be safe.
{   'shoulder_pan': {   'original goal_pos': -5.7,
                        'safe goal_pos': -5.571428571428571}}
·  note    at the rest pose
╭─ ✓ SUCCESS ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
│ Staged shoulder pan at approximately -119°, then swept slowly toward +119.6° in three 12-second segments, ending at -1°, within 1° of the range          │
│ midpoint. Issued stop and received confirmation that velocity was zeroed. This demonstrates stopping the segmented sweep halfway across its range, not   │
│ braking during an active move: move_joints waits for arrival.                                                                                            │
│ steps 8 · llm calls 10 · tokens 44699+533 · time 2m 07s (model 43.6 s) · model calls p50 3.2 s, p90 6.9 s, max 7.9 s · first motion at 30.5 s, after 3   │
│ model calls · cost $0.2853                                                                                                                               │
│ run dir  runs\20261106-170248-e118-brake-test                                                                                                            │
╰──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
```

</details>
