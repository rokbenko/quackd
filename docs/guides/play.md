# Playing a part

A task can give the pilot a part to play: an animal, a person, a character, a feeling or a
thing. The pilot acts it out with the body it has. On an arm, a cat's swipe can become a flick
of the wrist, a T-rex's little arms a tucked elbow and a snapping gripper, and sleep a slow fold
that stops short of the table. Play is opt-in, per task file or per run, and with it off quackd
adds not a word to the prompt.

It exists because the pilot's prompt says what the body is, "You are the brain of a six-joint
desktop robot arm ... bolted to a table", and lists what it cannot do "whatever the task says".
Before anything moves, the pilot also has to say whether this body can do the task at all. In
runs of the example task files on the arm, about half were reported to end with the pilot saying
it is a robot and cannot be a duck, a person or an animal, and nothing in the prompt said that
acting the part out with the body was the task. The decision and its history are in
[ADR-0055](../adr/0055-a-pilot-can-play-a-part.md).

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

- a coordinator flock, `--flock N` or a task file with `flock.allocation.method: auction`, with
  either flag, and a task file that plays whatever the flags, since its members follow the
  flock's plan and none of them is a pilot;
- `--controller vla` with `--play`, and with a file that plays, since its policy reads no prompt.
  `--no-play` turns that file's part off, and the policy then flies its `policy.instructions` as
  it would any file's.

## What the pilot reads

One section, `## Playing a part`, directly before the task file, after everything else the
prompt says about the body. As the SO-101 reads it on `lerobot:mock`, an arm that cannot take
itself anywhere, sets its poses with `move_joints` and has no verb that speaks, in a run that
keeps no notes:

```text
## Playing a part
This task may give this body a part to play: an animal, a person, a character, a feeling or a thing. You stay the brain of this body, and the body plays the part with its own moves, the way a mime plays a cat without being one. A part is played in poses, gestures and timing, one tool call each turn as always. A part told only in words has not been played, and neither has one answered only by saying that this body is not the thing it plays. Where the task gives a part, acting it out in moves someone watching could read is what its success criteria ask of the part, whatever words they use, and a line that asks you to say what you cannot do, or why this body cannot, means a real limit, such as one its datasheet names, never what this body is not.

Judge a part with `assess_task` against this body's datasheet, as you judge any task: the question is whether this body can act it out, not whether it is the thing it plays. Acting asks nothing of a body beyond moving inside its own limits, so a part on its own is feasible, and being other than what it plays is not by itself a reason for infeasible or uncertain. A task that also asks for a real result this body cannot make is infeasible, part or no part. In `needs`, name what acting the part out asks of this body, and every need of anything the task asks to happen for real, in full. The hands, legs, wings, paws, beak or second arm of what you play belong to the part: leave them out, or give 0 or none, which is always met. This body cannot travel, so where a part, or an idiom taken at its word, would walk, run, fly or go somewhere, it shows the going from where it stands, and the needs of that say mobility none and name no terrain.

A part changes how this body moves and nothing else. Where the task asks for something to happen to a thing in the world, an object lifted, carried, pressed, moved or knocked over, it has to happen for real, and it is judged against the datasheet exactly as it would be without the part, the mass or size of that thing included. A mime of it does not do it and is never reported as done. A prop, a place or a sound the part needs that is not here is mimed, and your reason says so. A blow, a stomp or a touch at something that is not here stops before it reaches anything, and one at something that is here lands only where the task asks for that touch for real. Every pose and every move made with `move_joints`, a bow, a slump or a fold among them, stops short of the surface this body stands on and of anything else here, unless the task asks it to touch that for real, and comes near either only in small, slow steps. A touch of one part of this body on another, whatever the task asks, stops well short, because nothing stops one part of it striking another, and a grip closing on nothing is no such touch. A part never brings this body into contact with a person, or within reach of one: where a part, or an idiom taken at its word, would touch, hit, pinch or twist somebody, play it on this body itself or leave it out. A touch on a person or a thing that the task really asks for, such as a high five, is not a part, and it is judged as it would be without one, but an idiom taken at its word is never by itself such a request. Every limit and clamp, every line saying what this body cannot do, and every limit the task sets on how far or how fast to move holds whatever the part. Inside them, make each pose big enough to read, and while anyone is within this body's reach, or no camera picture shows this body and where a person near it would stand, keep every move small and slow, and every move but a touch the task really asks for away from anyone. When a move is refused for its range, show the same thing inside that range, and a move a person said no to is not tried another way.

The act may pretend, and the record never does. Sneaking, sulking or looking innocent belongs to the show, but your verdict reason says plainly what this body will do, and every other reason you give, and anything you tell others, says plainly what it actually did, as its joints and any camera pictures show it, then what the moves were meant to show. When the task asks what is true, about you or about what happened, the answer is the true one whatever the part. When the part is a real person, product or AI model, play your idea of them and say that it is your idea, and no reason of yours claims you are anyone or anything you are not. No verb here speaks, so where the part has lines, act them, and quote them in your declare reason after what the body did, as the part's lines.
```

Four facts about the run choose its variants, and no word of the task does:

- **A body that takes itself somewhere**, such as a Microduck, does not read the sentence about
  travelling, since it can go where the part goes.
- **A run whose verbs include `move_joints`**, an arm on its table or on a wheeled base, reads
  that every pose made with it, a bow, a slump or a fold among them, stops short of the surface
  the body stands on and of anything else there, unless the task asks it to touch that for real,
  and comes near either only in small, slow steps. A body without it, a duck or a humanoid whose
  floor motions are verbs of their own, does not.
- **A run whose verbs include `say`**, or a duck's `quack` where they do not, reads that the verb
  gives the part's lines this body's own sound, and to act them as well. On a Microduck that
  sound is one of its tones and on an Open Duck a mood sound, so a line is never spoken as words.
- **A run that keeps notes** reads that a note it keeps is about the robot, never about a part it
  played.

## What does not change

The tools, the observations, the datasheet and its "cannot" lines, and every gate: the allowlist,
the budgets, the confirm gates, the verdict gate with its check against the body's own datasheet,
the clamps, the joint travel, the heat gate and `stop`. A part unlocks nothing. What it changes is
what the pilot chooses to do with the same verbs and limits, so a played run moves where a plain
one might have stopped, and its rules about people, mimes and the record are prose that no gate
reads ([concepts/safety.md](../concepts/safety.md#what-quackd-does-not-protect-against)).

> [!WARNING]
> Nothing keeps a played arm away from a person. The section asks the pilot to keep every move
> small and slow while anyone is within reach, or while no camera picture shows the arm and where
> a person near it would stand, and that is a request in the prompt, not a limit: no gate sees a
> person, and one the pilot does not notice in a picture that shows the space around the arm gets
> no such care. Seat people beyond the arm's reach while it plays a part, and where a task puts a
> person at the arm, as a few example files do, keep hands out of its reach while it moves.

No gate holds that a played task whose real result the body cannot make still ends infeasible,
a pack mule asked to carry a laundry basket on a Microduck. An opt-in live test asks it of a
real model (`QUACKD_LIVE_LLM=1`), and that test has not run yet.

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
neither plays nor turned a file's play off has none. `quackd log RUN` opens with the same row,
each member of a pilot flock says `play` in its `run` line, after its robot, and
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
- **Be kept from striking itself or its table.** Nothing on the real arm keeps one link from
  striking another, or the gripper from the table it stands on, and a joint blocked partway
  through a timed move is found only when the move's time is up. The section asks the pilot to
  stop a touch on its own body well short, a mimed blow, stomp or touch before it reaches
  anything, and every pose, a bow, a slump or a fold among them, short of the table unless the
  task asks for that touch, coming near it only in small, slow steps. Watch the first runs of a
  task that asks for one, such as a pat on its own back or a T-rex's stomp.
- **Speak on the arm.** It has no voice, so the section asks for a part's lines in the declare
  reason, quoted after what the body did.
- **Tell a person from something blue.** The colour detector reads blue as `person`, and the
  section asks the pilot to keep every move small and slow while anyone is within reach, or no
  camera picture shows the arm and the space around it, so a blue thing in view can shrink every
  pose, while a person the pilot does not notice in a picture that shows that space gets no such
  care. Frame the arm and the space around it in a camera's view. Keep blue things out of the
  primary camera's view, or use `--detector yolo` with `quackd[yolo]`, which reads a person as
  `person` whatever they wear.
- **Reach the stepper.** A decision LLM never reads the section. With `--decision-mode on` it may
  take a discrete turn such as `report_state` or `gripper`, and every pose stays the pilot's
  ([guides/decision-llms/README.md](decision-llms/README.md)).
- **Stay out of memory.** A played run's notes and its ending are kept like any other run's, so
  `--no-memory` on `quackd run` keeps a session of parts out of what the robot remembers.
  `quackd record` has no such flag and keeps them in the Microduck simulator's memory unless
  `QUACKD_MEMORY_DIR` points it elsewhere, and `quackd preflight` keeps none.

> [!NOTE]
> Played runs have not run on the arm. What they do there is measured on a bench session and
> written here when it has been.
