# Playing a part

A task can give the pilot a part to play: an animal, a person, a character, a feeling or a
thing. The pilot acts it out with the body it has. On an arm, a cat's swipe becomes a quick
flick of the wrist, a T-rex's little arms a tucked elbow and a snapping gripper, and sleep a
slow fold. Play is opt-in, per task file or per run, and a run that does not ask for it reads
exactly the prompt it always did.

It exists because the pilot's prompt says what the body is, "You are the brain of a six-joint
desktop robot arm ... bolted to a table", and lists what it cannot do "whatever the task says".
Before anything moves, the pilot also has to say whether this body can do the task at all. Given
"You are a cat", a careful model answered that question literally: an arm is not a cat. Nothing
in the prompt said that acting the part out with the body was the task. The decision and its
history are in [ADR-0051](../adr/0051-a-pilot-can-play-a-part.md).

## Turning it on

In a task file, `play: true` at `duck: 4` ([reference/duck-spec.md](../reference/duck-spec.md#play-v4)):

```yaml
---
duck: 4
name: t-rex
description: Be a T-rex.
verbs:
  allow: [report_state, move_joints, gripper, stop]
success:
  - You have shown what the task asks with the arm's joints and gripper, in moves someone watching could read, and your reason says what the arm did.
play: true
---
# Task
You are a T-rex. Show me.
```

On the command line, `--play` turns it on for one run of any task file, a `--goal` included, and
`--no-play` turns a file's `play: true` off for one run. Both work on `quackd run`, `quackd
record` and `quackd preflight`, and the flag wins over the file. No environment variable and no
field in `robots.json` sets it, so a robot never starts playing every task it is given.

```bash
quackd run --goal "You are a T-rex. Show me." --robot lerobot:mock --llm fake --play --no-memory
```

Each member of a pilot flock plays, whether the task file or `--play` asked. These are refused,
each with one line that says why:

- a coordinator flock, `--flock N` or a task file with `flock.allocation.method: auction`, whose
  members follow the flock's plan and have no pilot to read a part;
- `--controller vla`, with the flag or with a task file that plays, whose policy reads no
  prompt.

## What the pilot reads

One section, `## Playing a part`, directly before the task file, after everything else the
prompt says about the body. As the SO-101 reads it on `lerobot:mock`, an arm that cannot take
itself anywhere, with no `say`, in a run that keeps no notes:

```text
## Playing a part
This task may give this body a part to play: an animal, a person, a character, a feeling or a thing. You stay the brain of this body, and the body plays the part with its own moves, the way a mime plays a cat without being one. A part is played in poses, gestures and timing, one tool call each turn as always. A part told only in words has not been played, and neither has one answered only by saying that this body is not the thing it plays. Where the task gives a part, acting it out in moves someone watching could read is what its success criteria ask of the part, whatever words they use, and a line that asks you to say why this body cannot means a limit its datasheet names, never what this body is not.

Judge a part with `assess_task` against this body's datasheet, as you judge any task: the question is whether this body can act it out, not whether it is the thing it plays. Acting asks nothing of a body beyond moving inside its own limits, so a part on its own is feasible, and being other than the animal, person or thing it plays is not by itself a reason for infeasible or uncertain. A task that also asks for a real result this body cannot make is infeasible, part or no part. In `needs`, name what acting the part out asks of this body, and every need of anything the task asks to happen for real, in full. The hands, legs, wings, paws or second arm of what you play belong to the part: leave them out, or give 0 or none, which is always met. This body cannot travel, so where a part, or an idiom taken at its word, would walk, run, fly or go somewhere, it shows the going from where it stands, and the needs of that say mobility none and name no terrain.

A part changes how this body moves and nothing else. Where the task asks for something to happen to a thing in the world, an object lifted, carried, pressed, moved or knocked over, it has to happen for real, and it is judged against the datasheet exactly as it would be without the part, the mass or size of that thing included. A mime of it does not do it and is never reported as done. A prop, a place or a sound the part needs that is not here is mimed, and your reason says so. A part never brings this body into contact with a person, or toward one: where a part, or an idiom taken at its word, would touch, hit, pinch or twist somebody, play it on this body itself or leave it out. A touch the task really asks for, such as a high five, is not a part, and it is judged as it would be without one. Every limit and clamp, every line saying what this body cannot do, and anything the task says about how far or how fast to move holds whatever the part. Inside them, make each pose big enough to read, and while anyone is within this body's reach, keep every move small, slow and away from them. When a move is refused for its range, show the same thing inside the travel, and a move a person said no to is not tried another way.

The act may pretend, and the record never does. Sneaking, sulking or looking innocent belongs to the show, but your verdict reason says plainly what this body will do, and every other reason you give, and anything you tell others, says plainly what it actually did, as its joints and any camera pictures show it, then what the moves were meant to show. When the task asks what is true, about you or about what happened, the answer is the true one whatever the part. When the part is a real person, product or AI model, play your idea of them and say that it is your idea, and no reason of yours claims you are anyone or anything you are not. No verb here speaks, so where the part has lines, act them, and quote them in your declare reason after what the body did, as the part's lines.
```

Three facts about the run choose its variants, and nothing the task says does:

- **A body that takes itself somewhere**, such as a Microduck, does not read the sentence about
  travelling, since it can walk the part.
- **A body that offers `say`** reads that `say` gives the part's lines its own sound, and to act
  them as well. On a Microduck that sound is one of its tones and on an Open Duck a mood sound,
  so a line is never spoken as words.
- **A run that keeps notes** reads that a note it keeps is about the robot, never about a part it
  played.

## What does not change

The tools, the observations, the datasheet and its "cannot" lines, and every gate: the allowlist,
the budgets, the confirm gates, the verdict gate with its check against the body's own datasheet,
the clamps, the joint travel, the heat gate and `stop`. A part unlocks nothing. What it changes is
what the pilot chooses to do with the same verbs and limits, so a played run moves where a plain
one might have stopped, and its rules about people, mimes and the record are prose that no gate
reads ([concepts/safety.md](../concepts/safety.md#what-quackd-does-not-protect-against)).

That a played task whose real result the body cannot make still ends infeasible, a pack mule
asked to carry a laundry basket on a Microduck, is checked by an opt-in live test, not held by a
gate.

## Seeing it

The run's header says so when play is on, and when a run turned a file's play off:

```console
$ quackd run --goal "You are a T-rex. Show me." --robot lerobot:mock --llm fake --play --no-memory
+- goal --------------------------------------------------------------+
| provider  fake (scripted:goal)                                      |
| robot     lerobot:mock                                              |
| detector  color_blob on this machine                                |
| play      on, from --play                                           |
+- Ctrl-C or q stops the duck. Press it twice to quit at once. -------+
```

The row reads `on, from the task file`, `on, from --play` or `off, from --no-play`, and a run that
neither plays nor turned a file's play off has none. The log's `run` line ends with `play`, and
`quackd log RUN --prompt` shows the section. In the record, `run_start.play` holds whether the
pilot was told it may play and whose word that was, while `run_start.contract.play` stays the
task file's own: `--play` on a `duck: 1` file plays without rewriting a contract that cannot say
so.

## Over MCP

A client's own system prompt frames an MCP pilot, so the section cannot be added to it. Loading
a played file with `robot_load_duckfile` returns the same paragraphs as a `play` field, worded for
a chat, and the result's `note` says to read it before the verdict. A file that does not play
loads exactly as before. There is no `serve-mcp --play`, since it would make every file loaded in
the session play, and a part typed into the chat gets nothing from quackd. A task file adopted at
startup with `serve-mcp --duckfile` hands over neither its body nor its part.

## What a part cannot do

- **Go past the travel.** Every goal stays inside each joint's calibrated travel. On an arm whose
  recorded rest pose lies past it, a fold stops at the travel, so a sleeping cat needs the arm
  calibrated folded and its rest pose recorded again (sections 05 and 07 of
  [adapters/lerobot/first-run.md](../adapters/lerobot/first-run.md)).
- **Speak on the arm.** It has no voice, so lines go in the declare reason, quoted after what the
  body did.
- **Tell a person from something blue.** The colour detector reads blue as `person`, and a played
  run moves small and slow while anyone seems to be within reach. Keep blue things out of the
  primary camera's view, or use `--detector yolo`.
- **Reach the stepper.** A decision LLM never reads the section. With `--decision-mode on` it may
  take a discrete turn such as `report_state` or `gripper`, and every pose stays the pilot's
  ([guides/decision-llms/README.md](decision-llms/README.md)).
- **Stay out of memory.** A played run's notes and its ending are kept like any other run's, so
  `--no-memory` keeps a session of parts out of what the robot remembers.

> [!NOTE]
> Played runs have not run on the arm. What they do there is measured on a bench session and
> written here when it has been.
