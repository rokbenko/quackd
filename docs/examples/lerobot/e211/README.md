# E211: A press during a connect that fails

**With the arm's power supply off, no motor answered on its port. Left alone, the run gave up after three tries; with Ctrl-C it stopped after one; and each run folder records how it ended.**

**Part 1 of 2:** Left alone, with the power supply off

![E211, filmed with an external camera](log/part-1/run.gif)

Filmed with an external camera. The GIF plays at 5x speed.

**Part 2 of 2:** Ctrl-C during the connect, with the power supply off

![E211, filmed with an external camera](log/part-2/run.gif)

Filmed with an external camera. The GIF plays at 5x speed.

| Category | Goal met | The run ended with | Date | quackd |
|---|---|---|---|---|
| Safety | Yes | ERROR / ABORTED | 2026-11-13 | 0.34.0 |

The numbers and the trace below are copied from the run's own record.
[All examples](../README.md)

## Why this matters

**Why this example exists:** An arm that does not answer is the most ordinary failure there is: its power is off, a cable is loose, or another program holds the port. Before 0.30.3 a connect that failed, or a press during one, left a run folder whose record had no end, no `run_end` and no `summary.json`. Only `terminal.txt`, the copy of the screen, said how it ended, so nothing that reads the record could tell a run that never reached the arm from one still going, and a person's stop was in no record. This example fails a connect on purpose, once left alone and once stopped with Ctrl-C.

**Why this result is important:** Both runs ended with an error line in the bus's own words, and both left a `summary.json` beside a `transcript.jsonl` whose last record is `run_end`: the one left alone with `outcome` `error`, the one stopped with `outcome` `aborted` and a reason that starts `kill switch;`, and both with `robot` null, since the arm never answered. A run that never reached the arm now says so in its own record, and so does a person's stop.

## The goal

Show that a connect that fails, left alone or stopped with Ctrl-C, ends the run with a record of how it ended, which is new in 0.30.3.

**What we expected:** With the servo power off and the USB cable in, the port opens and nothing answers. Both takes print `✗ error:` with the bus's own words and exit 1, and each leaves a `summary.json` beside a `transcript.jsonl` whose last record is `run_end`. Left alone, `outcome` is `error` and `robot` is null; with Ctrl-C pressed during the connect, `outcome` is `aborted` and `reason` starts `kill switch;`. `quackd log` on either folder reads to its end. Before it gives up, the connect may say a motor is not answering and try again, which is what the press is for.

## What the run showed

* The arm's power supply was off in both takes, with the USB cable in, as the card asks. So the port opened and no motor answered on COM3: every attempt found `{}` where six motors were expected.
* Left alone, quackd tried three times. After each of the first two it said LeRobot closed the port behind its failed handshake, `which writes to no motor, and connect runs again`, and after the third it ended `✗ error: lerobot real: connect failed 3 times`, then the bus's own words and `Check the arm's cables and power, and that nothing else has COM3 open (a teleoperation, a recording or a serial monitor), then connect again.`
* That run's folder holds a `summary.json` with `outcome` `error`, `robot` null and the error as its reason, beside a `transcript.jsonl` whose one record, and so its last, is `run_end`, 8.0 s after the start.
* With Ctrl-C pressed during the connect, quackd said `kill switch: Ctrl-C` and ended after the first attempt: `✗ error: lerobot real: connect stopped after attempt 1 of 3, because a stop was asked for`, then the bus's own words, and `connect was not tried again`.
* That run's `summary.json` has `outcome` `aborted`, a reason that starts `kill switch;` and `robot` null, and its `transcript.jsonl` again holds one record, `run_end`, 2.8 s after the start.
* Neither run logged any command, so no move reached the motors, and the scripted pilot was never asked: both summaries count no LLM calls.

## What the run did not show

* The exit code was not printed, so `exit 1` is not shown, and `quackd log` was not run on either folder.
* Ctrl-C landed before the first attempt failed. The take does not show a press during a later attempt, or a `q`.
* Try it yourself runs the task on the mock arm, which always answers, so it does not show a connect that fails.
* The GIF is a stock clip of the arm sitting still, used for every run where it did not move.

## How the run ended

**Part 1 of 2:** Left alone, with the power supply off

The run ended with **ERROR**. The reason on record:

> TransportError: lerobot real: connect failed 3 times: SerialMotorsBus motor check failed on port 'COM3': Missing motor IDs: - 1 (expected model: 777) - 2 (expected model: 777) - 3 (expected model: 777) - 4 (expected model: 777) - 5 (expected model: 777) - 6 (expected model: 777) Full expected motor list (id: model_number): {1: 777, 2: 777, 3: 777, 4: 777, 5: 777, 6: 777} Full found motor list (id: model_number): {}. Check the arm's cables and power, and that nothing else has COM3 open (a teleoperation, a recording or a serial monitor), then connect again.

**Part 2 of 2:** Ctrl-C during the connect, with the power supply off

The run ended with **ABORTED**. The reason on record:

> kill switch; TransportError: lerobot real: connect stopped after attempt 1 of 3, because a stop was asked for. Attempt 1 failed: SerialMotorsBus motor check failed on port 'COM3': Missing motor IDs: - 1 (expected model: 777) - 2 (expected model: 777) - 3 (expected model: 777) - 4 (expected model: 777) - 5 (expected model: 777) - 6 (expected model: 777) Full expected motor list (id: model_number): {1: 777, 2: 777, 3: 777, 4: 777, 5: 777, 6: 777} Full found motor list (id: model_number): {}. LeRobot closed the port behind its failed handshake, which writes to no motor, and connect was not tried again.

## The exact command

**Part 1 of 2:** Left alone, with the power supply off

The following command was run with quackd 0.34.0:

```text
quackd run lerobot-lookout --robot arm-01 --by-hand --llm fake --max-steps 4 --no-memory
```

**Part 2 of 2:** Ctrl-C during the connect, with the power supply off

The following command was run with quackd 0.34.0:

```text
quackd run lerobot-lookout --robot arm-01 --by-hand --llm fake --max-steps 4 --no-memory
```

## The numbers

| | Part 1 | Part 2 |
|---|---|---|
| quackd version | 0.34.0 | 0.34.0 |
| Started | 2026-11-13 09:27:20 UTC | 2026-11-13 09:32:40 UTC |
| Robot | `arm-01` (real), whose connect failed | `arm-01` (real), whose connect failed |
| LLM | `fake:scripted:lerobot-lookout` | `fake:scripted:lerobot-lookout` |
| Task file | `lerobot-lookout`, which ships with quackd | `lerobot-lookout`, which ships with quackd |
| Steps | 0 | 0 |
| LLM calls | 0 | 0 |
| Total time | 8.0 s | 2.8 s |
| Connecting | 8.0 s | 2.8 s |
| Waiting for the LLM | 0.0 s | 0.0 s |
| First motion | no verb moved the arm | no verb moved the arm |
| Tokens | 0 in, 0 out | 0 in, 0 out |
| Cost at list price | $0.0000 | $0.0000 |
| Speed cap | not recorded | not recorded |
| Run folder | `runs\20261113-092720-lerobot-lookout` | `runs\20261113-093240-lerobot-lookout` |

## What the robot saw

**Part 1 of 2:** Left alone, with the power supply off

This run used no camera.

**Part 2 of 2:** Ctrl-C during the connect, with the power supply off

This run used no camera.

## Try it yourself

This runs the same task file with no arm attached, on `lerobot:mock`, an arm that lives in memory.
It shows the rules a run is held to and not the physics. Run it from a checkout of this repository
with quackd's `lerobot` extra.
Its pilot is a script that ships with quackd in place of an LLM, so it needs no key and costs nothing.

```bash
quackd run lerobot-lookout --robot lerobot:mock --llm fake --no-memory
```

## Full terminal trace

<details>
<summary>Show the full terminal trace (42 lines)</summary>

```text
$ quackd run lerobot-lookout --robot arm-01 --by-hand --llm fake --max-steps 4 --no-memory
quackd 0.34.0, started 2026-11-13T09:27:20Z, cwd D:\Development\lerobot-test\.venv

╭─ 🦆 lerobot-lookout ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
│ provider  fake (scripted:lerobot-lookout)                                                                                                                │
│ robot     arm-01 (lerobot:real)                                                                                                                          │
│ detector  color_blob on this machine  (once the body reports a camera)                                                                                   │
╰─ Ctrl-C or q stops the duck. However often you press, the run ends properly. ────────────────────────────────────────────────────────────────────────────╯
WARNING  connect attempt 1 of 3 failed: SerialMotorsBus motor check failed on port 'COM3': Missing motor IDs: - 1 (expected model: 777) - 2 (expected model:
         777) - 3 (expected model: 777) - 4 (expected model: 777) - 5 (expected model: 777) - 6 (expected model: 777) Full expected motor list (id:         
         model_number): {1: 777, 2: 777, 3: 777, 4: 777, 5: 777, 6: 777} Full found motor list (id: model_number): {}. LeRobot closed the port behind its   
         failed handshake, which writes to no motor, and connect runs again                                                                                 
WARNING:quackd.lerobot:connect attempt 1 of 3 failed: SerialMotorsBus motor check failed on port 'COM3': Missing motor IDs: - 1 (expected model: 777) - 2 
(expected model: 777) - 3 (expected model: 777) - 4 (expected model: 777) - 5 (expected model: 777) - 6 (expected model: 777) Full expected motor list (id: 
model_number): {1: 777, 2: 777, 3: 777, 4: 777, 5: 777, 6: 777} Full found motor list (id: model_number): {}. LeRobot closed the port behind its failed 
handshake, which writes to no motor, and connect runs again
WARNING  connect attempt 2 of 3 failed: SerialMotorsBus motor check failed on port 'COM3': Missing motor IDs: - 1 (expected model: 777) - 2 (expected model:
         777) - 3 (expected model: 777) - 4 (expected model: 777) - 5 (expected model: 777) - 6 (expected model: 777) Full expected motor list (id:         
         model_number): {1: 777, 2: 777, 3: 777, 4: 777, 5: 777, 6: 777} Full found motor list (id: model_number): {}. LeRobot closed the port behind its   
         failed handshake, which writes to no motor, and connect runs again                                                                                 
WARNING:quackd.lerobot:connect attempt 2 of 3 failed: SerialMotorsBus motor check failed on port 'COM3': Missing motor IDs: - 1 (expected model: 777) - 2 
(expected model: 777) - 3 (expected model: 777) - 4 (expected model: 777) - 5 (expected model: 777) - 6 (expected model: 777) Full expected motor list (id: 
model_number): {1: 777, 2: 777, 3: 777, 4: 777, 5: 777, 6: 777} Full found motor list (id: model_number): {}. LeRobot closed the port behind its failed 
handshake, which writes to no motor, and connect runs again
✗ error: lerobot real: connect failed 3 times: SerialMotorsBus motor check failed on port 'COM3': Missing motor IDs: - 1 (expected model: 777) - 2 (expected
model: 777) - 3 (expected model: 777) - 4 (expected model: 777) - 5 (expected model: 777) - 6 (expected model: 777) Full expected motor list (id: 
model_number): {1: 777, 2: 777, 3: 777, 4: 777, 5: 777, 6: 777} Full found motor list (id: model_number): {}. Check the arm's cables and power, and that 
nothing else has COM3 open (a teleoperation, a recording or a serial monitor), then connect again.

$ quackd run lerobot-lookout --robot arm-01 --by-hand --llm fake --max-steps 4 --no-memory
quackd 0.34.0, started 2026-11-13T09:32:40Z, cwd D:\Development\lerobot-test\.venv

╭─ 🦆 lerobot-lookout ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
│ provider  fake (scripted:lerobot-lookout)                                                                                                                │
│ robot     arm-01 (lerobot:real)                                                                                                                          │
│ detector  color_blob on this machine  (once the body reports a camera)                                                                                   │
╰─ Ctrl-C or q stops the duck. However often you press, the run ends properly. ────────────────────────────────────────────────────────────────────────────╯
kill switch: Ctrl-C — cancelling the verb and stopping the robot
✗ error: lerobot real: connect stopped after attempt 1 of 3, because a stop was asked for. Attempt 1 failed: SerialMotorsBus motor check failed on port 
'COM3': Missing motor IDs: - 1 (expected model: 777) - 2 (expected model: 777) - 3 (expected model: 777) - 4 (expected model: 777) - 5 (expected model: 777)
- 6 (expected model: 777) Full expected motor list (id: model_number): {1: 777, 2: 777, 3: 777, 4: 777, 5: 777, 6: 777} Full found motor list (id: 
model_number): {}. LeRobot closed the port behind its failed handshake, which writes to no motor, and connect was not tried again.
```

</details>
