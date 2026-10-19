# ADR-0054: A reflex may look through a decision LLM

**Status:** accepted · **Date:** 2026-10-19 · Amends [ADR-0003](0003-three-loops.md) (a composite verb never calls the *pilot*, and the steering tier gains a loop that asks a decision LLM), [ADR-0008](0008-perception-features-not-frames.md) (a decision LLM may be shown the primary frame inside `watch`, on a run that says so) and [ADR-0012](0012-safety-executor.md) (a `stop` from any caller ends a verb that asks) · Implemented in `quackd/verbs/watch.py` and `quackd/agent/decision/eyes.py`, with changes in the executor, the registry, the loop, the CLI, `quackd log` and the Microduck adapter · Documented in [docs/adapters/microduck/README.md](../adapters/microduck/README.md#watch-a-head-that-keeps-something-in-view)

## Context

OpenAI's launch video for its Decisions API has a robot segment, "Guiding Lavender the robot", and
Lavender is a Hugging Face Microduck, the body quackd has an adapter for. While a voice model talks,
the Decisions API looks at camera frames and the head turns to keep "the apple", then "the fruit",
in view. OpenAI published no code for it.

quackd could not do that, for four reasons each written down on purpose. The pilot is too slow: a
turn takes 3 to 8 seconds, and a head that follows a thing has to answer in about one. The stepper
([ADR-0040](0040-a-discrete-stepper-in-front-of-the-model.md)) is quick enough and cannot repeat
itself: its `repeat` gate and its streak limit end any run of identical calls, which tracking is.
The steering loop is fast and blind to words: `go_to` and `search_scan` close on a detector's
labels, a closed set in which "the apple" is not one. And two ADRs said no on principle:
[ADR-0003](0003-three-loops.md), "Composite verbs never call the LLM", and
[ADR-0008](0008-perception-features-not-frames.md), the LLM "needs a sentence, not a pixel grid".

[ADR-0053](0053-a-decision-llm-may-be-shown-the-camera.md) made one row a decision LLM that can see,
`openai`, behind `--decision-frames`, with Rok's rule for every frame: only when the run asks, only
for a row that can see, only at OpenAI's own hosts, and the record keeps what was sent. That is the
first decision LLM quackd names that could answer where a described thing is in a picture, and
OpenAI's own sub-second typed answers are what the reflex needs.

## Decision

- **One verb on one body.** `watch(target, duration_s=15)`: a target of 1 to 80 characters on one
  line with no control character in it, and 2 to 30 seconds. The engine is in the core,
  `quackd/verbs/watch.py`, with a `HeadSpec` of a body's head numbers, and a body gives itself the
  verb with its own (`watch_verb`). Only the Microduck does, declared only on a duck whose camera
  worked at connect, with the precondition `not_fallen`. It is `safe` and `composite`, never
  `read_only`, so a dry run sends no frame, with a 45 s timeout, and it runs before a verdict, as
  `gaze` does, since it moves the head and nothing else. The stepper can never take it: a
  free-string target is a value and never a choice.
- **Only with eyes.** It looks through `quackd/agent/decision/eyes.py`, built only through
  `frames.may_send_frames`, the gate every frame passes, so only quackd's own client for OpenAI's
  Decisions API, built with frames, at OpenAI's own address, is ever handed one. On a run without
  that, or through frames nobody can say are upright and fresh, mediad's WebRTC track among them, it
  is withdrawn when the robot connects, by a new `VerbRegistry.remove` that refuses to remove
  `stop`, with a note where the task or the flags asked for it. A task that requires it is refused
  before the robot connects on a run with nothing to look through, and once it has, before anything
  moves, on a camera that cannot be used. It is independent of `--decision-mode`:
  `--decision-mode off --decision-frames` is `watch` with no stepper.
- **Ask where, never how.** One request a look, two `choice` questions: `where`, the fifths of the
  picture's width or `out_of_view`, and `height`, its thirds or `out_of_view`, not asked of a head
  that cannot tilt. The instructions are fixed and prefer the thing nearest the middle, and the
  target goes in the state as a description somebody wrote, never in the instructions.
- **Gate on the distribution, never on the vendor's `confidence`**, which OpenAI do not define and
  which is recorded: 0.5 or more on `out_of_view` is not seen, 0.6 or more on the sectors spread no
  wider than 0.25 of the picture is seen at their mean weighted by probability, and anything else
  holds the head still.
- **quackd's own control law and envelope.** The turn is `atan(2x tan(H/2))` through the lens, times
  a gain of 0.6, nothing within 0.08 of the middle, at most 15° of yaw and 8° of pitch a look, and
  the head held to the body's envelope, on the Microduck 45° either way, 14° up and 11.5° down until
  a duck is measured, sent as `gaze` sends a look. The claim at the start of a call is no look: it
  brings a head that something else turned past the envelope back inside it in one move, up to 45°
  on a Microduck after a `gaze` to 90°. The lens is `--fov-deg`, else the camera's own, else an
  assumed 40°, and one that was assumed, an alpha's `/frame` among them, is named as assumed in
  `run_start.watch` and in the result.
- **Timing.** One request out at a time, looks at least 0.5 s apart on the robot's clock and
  requests at least 0.5 s apart on the wall's, and 60 a call at most. The head is claimed first and
  given the body's glance fade and a settle before the first frame, 1.9 s on a robotd that glances,
  and again after any gap in which a glance could have begun. A frame is used only when it is known
  to be taken after the head settled, never twice, and waited for as long as the camera needs: on a
  duck that is mediad's `/frame`, whose frames quackd stamps with when it asked for each, and its
  WebRTC track, whose frames carry no time, offers no `watch`. Each answer is bounded at 1.5 s, the
  head is sent its target again at least every second while anything is waited for, and a
  simulator's clock is run for as long as the answer took, with a cap on the wall's clock.
- **Exits, each named, and none sends the head anywhere.** `duration`, `lost` after 4 looks, `edge`
  after 3, `errors` after 3, `refused`, `camera`, `fallen`, `blind` and `held`, `look_refused`,
  `stopped`, `budget`, `error` for something raised, and `abort`, `timeout` and `cancel` from the
  executor, which says which as it cancels. The body is read before every command the head is sent,
  the claim, every move and every hold, and the head is moved only on one that reads as standing or
  sitting, the allowlist quackd's own gaze hold keeps: anything else is `held`.
  `VerbContext.interrupted` carries the run's time budget and a `stop` asked for from beside the
  call.
- **Record before send.** Each frame is written as `sent/watch-NNNN.jpg`, then a `watch_frame` line
  naming it, its size and its sha256 is written through to the transcript, and only then is the
  request handed to the client, so a process killed with the request out has recorded the frame. A
  disk that refuses the copy leaves no partial file and records `kept: false`. Each look's
  `watch_tick` row is written from a `finally`. Beside them, `run_start.watch` and the summary's
  `decision.watch`, whose usage and cost are folded into the decision block's totals.

## Why not

- **A detector that answers open words.** It would make a head reflex a core verb that every body
  with a head and a camera gets, breakable heads included, and its freshness rule took frames from
  before the head moved, and its `search_scan` path would have sent frames distorted by glances.
- **Asking which way to turn.** The model would have to know the lens, the mount, the reach and the
  gain, and every mistake about where a thing is would be a motion decision.
- **More than one request out.** A second answer about a frame taken before the first answer's turn
  landed corrects the same error twice.
- **Steering the head in joint space** with `robot.head`. That is the head's kinematics, which
  quackd does not carry, and `robot.look`, which robotd solves, is fast enough at this pace.
- **A reflex running beside the pilot**, as the demo's voice model talked while the Decisions API
  looked. It would fight `go_to`'s and `search_scan`'s own claims on the head and escape the
  executor's budgets, gates and kill switch, which hold a verb and nothing else.
- **Gating on `confidence`.** OpenAI do not define it, and the one comparison anybody has posted,
  on OpenAI's own forum, found this model less calibrated than Jev.
- **A shadow mode.** A head that does not move cannot close the loop it would be measuring, and the
  frames kept under `sent/` are what a threshold is calibrated on offline.
- **Over MCP in this decision.** `serve-mcp` writes no record of its own, and Rok's rule needs one.
  It names no decision LLM, so its sessions withdraw `watch`, until a session record exists.

## Consequences

- A verb as well as the stepper may now send a camera frame, under the same flag and the same gate,
  and the record keeps both: `frames/NNNN-decision.jpg` for the stepper, and `sent/watch-NNNN.jpg`
  and `watch_frame` for `watch` ([SECURITY.md](../../SECURITY.md)).
- ADR-0003's line becomes "composite verbs never call the pilot": `watch` asks a decision LLM two
  closed questions a look, and quackd computes and clamps every move. ADR-0008's detector is still
  the only perception in the 10 Hz loop, and `watch`'s answer is a sector, never a `Detection`.
- A stop asked for from beside a running verb now reaches a verb that asks
  (`VerbContext.interrupted`). Only `watch` asks, and `go_to` does not yet.
- Every number here is a starting value: the gate's three, the gain and the dead band, the head's
  envelope, the 1.5 s answer and the settle. Every row records what it was decided on, so they move
  on what the rows say.
- **Nothing here has run on a duck or against OpenAI.** In the suite the head closes on the ball on
  both simulators through quackd's own client for OpenAI's Decisions API, on a fake of its SDK that
  finds the ball in each JPEG it is handed. How long a look with a picture takes, what it costs, and
  how well a model says where a thing is are unmeasured, and the head's own numbers are owed to the
  bench.
