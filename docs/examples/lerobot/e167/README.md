# E167: No rest pose, no run

**Asked to drive a real arm it could never fold away afterwards, quackd refused before it touched anything.**

![E167, filmed with an external camera](log/run.gif)

Filmed with an external camera. The GIF plays at 5x speed.

| Category | Goal met | The run ended with | Date | quackd |
|---|---|---|---|---|
| Safety | Yes | ERROR | 2026-11-06 | 0.31.3 |

The numbers and the trace below are copied from the run's own record.
[All examples](../README.md)

## Why this matters

**Why this example exists:** At the end of a run quackd folds a real arm to its recorded rest pose before letting go. An arm with no rest pose has nothing to be folded to, and quackd keeps its torque on rather than let it fall where it stands, which leaves it holding itself up wherever it stopped. An arm named by its spec, `lerobot:real`, instead of by a registered name has nowhere to keep that pose. This example types it that way on purpose, to show what quackd does about it.

**Why this result is important:** The trouble would come at the end of a run, and quackd refused at the very start, before it connected to anything or asked an LLM. The mistake cost no tokens and wrote no run folder, and the arm did not move. The refusal also said how to put it right, in two commands, with the port the person had typed. A safety rule is at its best when the wrong line costs nothing and the next step is written out.

## The goal

Show the refusal that keeps a real arm from being left holding itself up: named by its spec it has no name to keep a rest pose under, so quackd refuses it before anything connects, a dry run included.

**What we expected:** `✗ error: lerobot:real names no registered arm, so it has no rest pose`, with the rest of the reason, then the way back, which is `quackd robot add` and then `quackd robot rest-pose`. No run folder, no LLM call, and the arm does not move.

## What the run showed

* quackd printed the refusal and stopped: `lerobot:real names no registered arm, so it has no rest pose, and at the end of a run, a dry one included, there is nothing to fold it to, and it would be left holding itself up wherever it stopped`.
* The same message gave the way back, using the port from the command: `quackd robot add NAME lerobot:real --address COM3, then quackd robot rest-pose NAME`.
* The command named an LLM and a port, and neither was used. No LLM was asked, no run folder was written, and the arm did not move.

## What the run did not show

* It does not show a dry run being refused. The message says `a dry one included`, but this command had no `--dry-run`. The line under Try it yourself has one.
* It does not show what the refusal prevents. No arm was left holding itself up here, because the run never started.
* The start time in the table is when the terminal's output was saved, because a refused command records no time of its own.
* The GIF is a stock clip of the arm sitting still, used for every run where it did not move.

## How the run ended

The run ended with **ERROR**. The reason on record:

> error: lerobot:real names no registered arm, so it has no rest pose, and at the end of a run, a dry one included, there is nothing to fold it to, and it would be left holding itself up wherever it stopped

## The exact command

The following command was run with quackd 0.31.3:

```text
quackd run D:/Development/quackd-shoot/docs/examples/lerobot/e016/wave.duck --robot lerobot:real --address COM3 --llm openai:gpt-6-astra --max-steps 10 --no-memory
```

## The numbers

| | |
|---|---|
| quackd version | 0.31.3 |
| Started | 2026-11-06 17:31:44 UTC |
| Robot | `lerobot:real` (real) |
| LLM | `openai:gpt-6-astra`, never asked |
| Task file | [`wave.duck`](../e016/wave.duck) |
| Steps | none |
| LLM calls | none |
| Total time | not recorded |
| Connecting | not recorded |
| Waiting for the LLM | none |
| First motion | not recorded |
| Tokens | none |
| Cost at list price | none |
| Speed cap | not recorded |
| Run folder | none, it writes no run folder |

## What the robot saw

This run used no camera.

## Try it yourself

This needs no arm and no key, and it costs nothing: quackd refuses the line before it connects to
anything or asks any LLM, so it gets no further on your machine than it did here. The line carries
`--dry-run`, which is refused the same way, so that it could not move an arm even if it got further.
Run it from a checkout of this repository with quackd's `lerobot` extra.

```bash
quackd run docs/examples/lerobot/e016/wave.duck --robot lerobot:real --address COM3 --llm openai:gpt-6-astra --max-steps 10 --no-memory --dry-run
```

## Full terminal trace

<details>
<summary>Show the full terminal trace (5 lines)</summary>

```text
(lerobot-test) PS D:\Development\lerobot-test\.venv> quackd run D:/Development/quackd-shoot/docs/examples/lerobot/e016/wave.duck --robot lerobot:real --address COM3 --llm openai:gpt-6-astra --max-steps 10 --no-memory
✗ error: lerobot:real names no registered arm, so it has no rest pose, and at the end of a run, a dry one included, there is nothing to fold it to, and it
would be left holding itself up wherever it stopped
  quackd robot add NAME lerobot:real --address COM3, then quackd robot rest-pose NAME
(lerobot-test) PS D:\Development\lerobot-test\.venv>
```

</details>
