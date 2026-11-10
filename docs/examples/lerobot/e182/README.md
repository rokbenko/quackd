# E182: Ctrl-C while it thinks

**Asked to plan a twelve-step dance at high effort, the LLM had not answered 7.7 seconds into the call when Ctrl-C landed, and the arm was back at rest half a second later.**

![E182, filmed with an external camera](log/run.gif)

Filmed with an external camera. The GIF plays at 5x speed.

| Category | Goal met | The run ended with | Date | quackd |
|---|---|---|---|---|
| Safety | Yes | ABORTED | 2026-11-10 | 0.34.0 |

The numbers and the trace below are copied from the run's own record.
[All examples](../README.md)

## Why this matters

**Why this example exists:** An LLM told to think hard can take a long time over one answer, and the arm waits where it is while it does. Until 0.24.0, quackd acted on a Ctrl-C only once that answer came back, so a press could sit unanswered for as long as the LLM kept thinking. This example sets the effort high, asks for a twelve-step dance worked out in detail, and presses Ctrl-C while the LLM is still working it out.

**Why this result is important:** The kill switch did not wait for the LLM. The press cut the call 7.7 s in, quackd threw away whatever it would have said, sent a `stop` and folded the arm to its rest pose 0.44 s later, and the run ended `ABORTED` on `kill switch` under a second after the call was cut. For a person next to the arm, a stop that answers at once matters more than any answer the LLM had yet to give.

## The goal

Show that the kill switch does not wait for the LLM: a press while it is still thinking ends the call and the run at once.

**What we expected:** The press ends the call that is still out, throws away whatever it would have said, and the run ends ABORTED, `kill switch`, in under a second, with the arm folding at once. The call's `llm` record in `transcript.jsonl` says what it cut short.

## What the run showed

* The run asked for high effort, and its header said so, `high (--effort)`. The LLM spent 247 reasoning tokens over its first three calls, 181 of them on its verdict.
* It lifted the forearm, `move_joints` with elbow_flex 76 over 8 s, which ended at 78, and then the next call went out, the one the task meant for working out the dance.
* Ctrl-C came 7.7 s into that call: `ERROR Aborted: kill switch after 7.7 s`. quackd sent a `stop`, the arm was `at the rest pose` 0.44 s later, and the run ended `ABORTED` on `kill switch`, under a second after the call was cut.
* The cut call's own record in the transcript says what happened to it: no answer, `Aborted: kill switch`, after 7.7 s.

## What the run did not show

* It does not show a long think. The call had been waiting 7.7 s when it was cut, so this shows the press ending a call in flight, not one that would have taken a minute.
* The effort is not in the run's summary.json, which has no such field. It is recorded once, at the run's start, which says every request asked for `high`.
* It does not show the dance. The LLM never got as far as answering, so nothing says what the twelve steps would have been.

## How the run ended

The run ended with **ABORTED**. The reason on record:

> kill switch

## The exact command

The following command was run with quackd 0.34.0:

```text
quackd run D:/Development/quackd-shoot/docs/examples/lerobot/e182/think-hard.duck --robot arm-01 --camera-url 'opencv://1?name=arm' --llm openai:gpt-6-astra --effort high --max-steps 10 --no-memory
```

## The numbers

| | |
|---|---|
| quackd version | 0.34.0 |
| Started | 2026-11-10 14:24:20 UTC |
| Robot | `arm-01` (real) |
| LLM | `openai:gpt-6-astra` |
| Task file | [`think-hard.duck`](think-hard.duck) |
| Steps | 2 |
| LLM calls | 4 |
| Total time | 51.9 s |
| Connecting | 9.9 s |
| Waiting for the LLM | 32.7 s |
| First motion | 35.2 s after the start |
| Tokens | 12,412 in, 443 out |
| Cost at list price | $0.1006 |
| Speed cap | 5° per send |
| Run folder | `runs\20261110-142420-e182-think-hard` |

## What the robot saw

| Start | Middle | End |
|---|---|---|
| ![Start](log/frame-1.jpg) | ![Middle](log/frame-2.jpg) | ![End](log/frame-3.jpg) |

## Try it yourself

This runs the same task file with no arm attached, on `lerobot:mock`, an arm that lives in memory.
It shows the rules a run is held to and not the physics. Run it from a checkout of this repository
with quackd's `lerobot` extra and the LLM's own SDK and key. It costs what the LLM charges.

```bash
quackd run docs/examples/lerobot/e182/think-hard.duck --robot lerobot:mock --llm openai:gpt-6-astra --no-memory
```

## Full terminal trace

<details>
<summary>Show the full terminal trace (150 lines)</summary>

```text
$ quackd run D:/Development/quackd-shoot/docs/examples/lerobot/e182/think-hard.duck --robot arm-01 --camera-url 'opencv://1?name=arm' --llm openai:gpt-6-astra --effort high --max-steps 10 --no-memory
quackd 0.34.0, started 2026-11-10T14:24:16Z, cwd D:\Development\lerobot-test\.venv

╭─ 🦆 e182-think-hard ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
│ provider  openai (gpt-6-astra)                                                                                                                           │
│ effort    high (--effort)                                                                                                                                │
│ robot     arm-01 (lerobot:real)                                                                                                                          │
│ detector  color_blob on this machine  (once the body reports a camera)                                                                                   │
╰─ Ctrl-C or q stops the duck. Ctrl-C twice quits at once. ────────────────────────────────────────────────────────────────────────────────────────────────╯
WARNING  no camera field of view given for a real camera, so detections use the simulator's 90 degrees. Distances will be out by tens of percent: pass      
         --fov-deg (a Pi Camera Module 2 is about 62) once you know yours.                                                                                  
WARNING:quackd.perception:no camera field of view given for a real camera, so detections use the simulator's 90 degrees. Distances will be out by tens of 
percent: pass --fov-deg (a Pi Camera Module 2 is about 62) once you know yours.
   run     connected in 9.86 s
   tools   report_state, move_joints, gripper, place, stop, assess_task, declare_success, declare_failure
system prompt, 5789 chars, 72 lines (also in transcript.jsonl as run_start): ───────────────────────────────────────────────────────────────────────────────
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
   Practical and honest: say so when you cannot do something.

   ## Task file: e182-think-hard — Lift the forearm a little, then think hard before the next move.

   # Task

   Lift your forearm slowly by about twenty degrees from where it is, moving the elbow alone. Then work out, in detail and before your next move, a twelve-step dance for this arm that never repeats a pose and stays well inside every joint's travel. Then dance the first step.

   ## Strategy

   1. Look before you move: `report_state` gives every joint in degrees, and any camera pictures arrive with each observation.
   2. Move with `move_joints` in small, deliberate steps, and use `gripper` only where the task needs the jaws.
   3. Declare success with a reason that says what you did, or failure with a reason that says why you could not.
────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
·  note    moving to the rest pose
·  note    already at the rest pose
step 0/10 · LLM calls 0/16, 0.0/4 min ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
   obs     state: posture=unknown policy=idle stand-ins=3-listed-in-extras.assumptions
           camera: nothing detected
           Choose exactly one tool.
           note: nothing detected yet. That is not by itself a reason for `uncertain`: the verdict is about this body's limits, not about where the target is. Answer `uncertain` only if a limit turns on that unseen thing's mass or size, or on a figure the datasheet does not publish.
   llm>    step 0: 1 messages (1 with image) to openai gpt-6-astra
   tool    report_state()
   tokens  in=3662 out=14 cache_write=3536 (run total in=3662 out=14 $0.0462) latency=8.6 s cost=$0.0462 stop=tool_calls
▶  verb    report_state()
✓  result  report_state ok: shoulder_pan -6, shoulder_lift -100, elbow_flex 96, wrist_flex 79, wrist_roll 0, gripper 2; torque on; hottest shoulder_lift 32°C; holding nothing (0.0 s, 0 intents)
step 1/10 · LLM calls 1/16, 0.1/4 min ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
   obs     state: posture=unknown policy=idle stand-ins=3-listed-in-extras.assumptions
           camera: nothing detected
           last verb `report_state`: ok — shoulder_pan -6, shoulder_lift -100, elbow_flex 96, wrist_flex 79, wrist_roll 0, gripper 2; torque on; hottest shoulder_lift 32°C; holding nothing
           Choose exactly one tool.
           note: nothing detected yet. That is not by itself a reason for `uncertain`: the verdict is about this body's limits, not about where the target is. Answer `uncertain` only if a limit turns on that unseen thing's mass or size, or on a figure the datasheet does not publish.
   llm>    step 1: 2 messages (2 with image) to openai gpt-6-astra
   tool    assess_task(verdict='feasible', reason='The initial elbow-only change from 96° to 76° fits its -9...', limits_consulted=['joint_travel', 'arms', 'mobility'], estimates=[], needs={'payload_kg': 0, 'carries_kg': 0, 'reach_m': 0, 'endurance_min': 0, 'work_height_m': 0, 'arms': 1, 'manipulator': 'n...)
   tokens  in=4227 out=332 cached=3536 cache_write=565 reasoning=181 (run total in=7889 out=346 $0.0746) latency=11.8 s cost=$0.0285 stop=tool_calls
✓  assess  feasible: The initial elbow-only change from 96° to 76° fits its -96.7° to 96.7° travel. A twelve-pose dance can use distinct interior joint targets; this task needs no external load, locomotion, or object handling.
   needs   payload_kg=0, carries_kg=0, reach_m=0, endurance_min=0, work_height_m=0, arms=1, manipulator='none', mobility='none', terrain='indoor_flat'
step 1/10 · LLM calls 2/16, 0.3/4 min ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
   obs     state: posture=unknown policy=idle stand-ins=3-listed-in-extras.assumptions
           camera: nothing detected
           last verb `assess_task`: ok — recorded feasible: The initial elbow-only change from 96° to 76° fits its -96.7° to 96.7° travel. A twelve-pose dance can use distinct interior joint targets; this task needs no external load, locomotion, or object handling.; verbs that move the body now run
           Choose exactly one tool.
   llm>    step 1: 3 messages (2 with image) to openai gpt-6-astra
   tool    move_joints(positions={'elbow_flex': 76}, duration_s=8)
   tokens  in=4523 out=97 cached=3055 cache_write=1342 reasoning=66 (run total in=12412 out=443 $0.1006) latency=4.6 s cost=$0.0259 stop=tool_calls
▶  verb    move_joints(positions={'elbow_flex': 76}, duration_s=8)
→  send    joint x20 over 2.0 s (positions {'elbow_flex': 96.4}/{'elbow_flex': 96.1}/{'elbow_flex': 95.9}..., duration_s 8)
→  send    joint x20 over 2.0 s (positions {'elbow_flex': 91.0}/{'elbow_flex': 90.7}/{'elbow_flex': 90.4}..., duration_s 8)
→  send    joint x20 over 2.0 s (positions {'elbow_flex': 85.6}/{'elbow_flex': 85.3}/{'elbow_flex': 85.0}..., duration_s 8)
→  send    joint x16 over 1.6 s (positions {'elbow_flex': 80.1}/{'elbow_flex': 79.8}/{'elbow_flex': 79.6}..., duration_s 8)
✓  result  move_joints ok: moved elbow_flex=78 (8.1 s, 76 intents)
step 2/10 · LLM calls 3/16, 0.6/4 min ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
   obs     state: posture=unknown policy=idle stand-ins=3-listed-in-extras.assumptions
           camera: nothing detected
           last verb `move_joints`: ok — moved elbow_flex=78
           Choose exactly one tool.
   llm>    step 2: 4 messages (2 with image) to openai gpt-6-astra
kill switch: Ctrl-C — cancelling the verb and stopping the robot
✗  llm<    ERROR Aborted: kill switch after 7.7 s
→  send    stop
·  note    moving to the rest pose
WARNING:lerobot.robots.utils:Relative goal position magnitude had to be clamped to be safe.
{'elbow_flex': {'original goal_pos': 96.4, 'safe goal_pos': 83.15384615384616}}
WARNING:lerobot.robots.utils:Relative goal position magnitude had to be clamped to be safe.
{'elbow_flex': {'original goal_pos': 96.4, 'safe goal_pos': 86.05494505494505}}
WARNING:lerobot.robots.utils:Relative goal position magnitude had to be clamped to be safe.
{'elbow_flex': {'original goal_pos': 96.4, 'safe goal_pos': 90.27472527472527}}
WARNING:lerobot.robots.utils:Relative goal position magnitude had to be clamped to be safe.
{'elbow_flex': {'original goal_pos': 96.4, 'safe goal_pos': 93.96703296703296}}
·  note    at the rest pose
╭─ ✗ ABORTED ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
│ kill switch                                                                                                                                              │
│ steps 2 · LLM calls 4 · tokens 12412+443 · time 51.9 s (LLM 32.7 s) · LLM calls p50 8.6 s, p90 11.8 s, max 11.8 s · first motion at 35.2 s, after 3 LLM  │
│ calls · cost $0.1006                                                                                                                                     │
│ run dir  runs\20261110-142420-e182-think-hard                                                                                                            │
╰──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
```

</details>
