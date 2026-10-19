# ADR-0053: A decision LLM may be shown the camera, on one row, when the run says so

**Status:** accepted · **Date:** 2026-10-19 · Amends [ADR-0040](0040-a-discrete-stepper-in-front-of-the-model.md) (what the stepper is sent, and what its record and its summary keep) and [ADR-0052](0052-openais-decisions-api-is-a-second-wire-format.md) (`openai` may be shown a picture, where it was sent text alone) · Implemented in `quackd/agent/decision/frames.py`, with changes in `base.py`, the catalogue, the factory, `decisions_api.py`, the stepper, the loop, the CLI and `quackd log` · Documented in [docs/guides/decision-llms/openai.md](../guides/decision-llms/openai.md#showing-it-the-camera---decision-frames)

## Context

[ADR-0040](0040-a-discrete-stepper-in-front-of-the-model.md)'s stepper reads a turn as text: a
named state of short English fields, with the camera in it as one line summarising the
detections. Its own docstring said "Never a picture", every row's `sees` was false, and
[SECURITY.md](../../SECURITY.md) called a picture reaching a decision LLM a security issue. A turn
that needed eyes escalated to the model.

[ADR-0052](0052-openais-decisions-api-is-a-second-wire-format.md) added the first decision LLM
quackd names whose API takes a picture. OpenAI's `POST /v1/decisions` takes inline images beside
the text, as `input_image` parts of a user message, at most 128 a request, and OpenAI's own launch
video for it has a chapter on making decisions from images and one guiding a Microduck. ADR-0052
kept that row blind and said that whether a camera frame should ever reach a decision LLM was a
separate decision, which would need a flag of its own. This is that decision.

Rok decided it on 2026-10-08, in four parts: a camera frame reaches a decision LLM only when the run
asks with an explicit flag, only for a row that can see, only at OpenAI's own hosts, and the record
keeps what was sent.

## Decision

- **A flag, off unless a run says so.** `--decision-frames`, `--no-decision-frames` and
  `QUACKD_DECISION_FRAMES` (`1`, `true`, `yes` or `on`, or `0`, `false`, `no` or `off`, and any
  other word stops the run), resolved in `factory.resolve_decision_frames` while the CLI reads its
  flags. It is refused there, before anything connects and on every machine, when no decision LLM
  is named, when the one named cannot see, and when its address is not one of OpenAI's hosts, a
  server of your own on this machine included. `--decision-llm off` turns it off, and
  `quackd record` pins it off. It is independent of `--decision-mode`, because whether a frame may
  go is a different question from how much the stepper's answer may do, and the CLI builds the
  decision LLM when the mode is not off or frames are on; with the mode off the stepper is asked
  nothing, so nothing is sent. It is independent of `--vision`, which is the pilot's. One line
  before the robot connects names the host frames go to, or, with the mode off, says none will.
- **Sight is data.** `DecisionSpec.sees` is true on `openai` and on no other row, and a plugin's
  row is built with it false whatever the plugin says. `base.SeeingDecisionLLM` adds `frames:
  bool` and `decide(state, questions, *, frames=(), timeout_s=None)`; every other row and every
  plugin keeps the two-argument `decide` and is never handed the keyword.
- **One gate for every frame.** `frames.may_send_frames(llm)` is true only for quackd's own
  `DecisionsAPILLM`, that class and no subclass, built with `frames`, at an address
  `decisions_api.is_openai_host` accepts, the one function that also decides where
  `OPENAI_API_KEY` goes and what a turn costs. `Stepper.build(frames=True)` refuses any other
  decision LLM, and the stepper asks the gate again before every frame, so a stepper put together
  by hand shows nothing to a plugin or a server of your own either. The client itself refuses
  `frames` at an address that is not OpenAI's, and its `decide` refuses a frame from a client not
  built for one, or more than 128, before anything is sent.
- **The frame.** The primary camera's picture that the turn's observation read, the one the
  detections describe, which the loop used to discard, encoded in a worker thread by
  `frames.encode`: an RGB copy, its longest side at most 512 pixels and never enlarged, as a JPEG
  at quality 85, sent at `detail: "low"`. A JPEG over 256 kB is skipped with a reason, and no
  picture reaches that at these settings. It goes as an `input_image` part after the state's
  `input_text`, in one user message, the shape OpenAI's reference and the guide's own example
  give, and never inside the state, which gains one text field, `picture`, saying an image went
  beside it. `TIMEOUT_S` stays 1.0 s for a turn with a frame until a measurement says otherwise.
- **Record before send.** Before `decide` is awaited, the JPEG is written as
  `frames/NNNN-decision.jpg`, under the number of the step's own PNG, a `decision_frame` line naming
  it is written to the transcript and flushed, the turn's `frame` block is set,
  `{camera, media_type, width, height, bytes, detail, sha256, path, kept}`, and the frame counted in
  `frames_sent`. The loop emits the `decision` event from a `finally`, so a 429, a timeout, an abort
  or a cancel still records the frame, and the line written before is what names it when the process
  dies first. A process that dies between the copy and the line leaves a copy that nothing was sent
  with, never a frame sent with nothing naming it. A frame handed over is counted, though a
  connection refused or timed out first never carried it, since from here the two cannot be told
  apart. An `OSError` on the write removes what part of the copy was written and sets `kept: false`,
  the frame still going, and never ends the run. A turn asked without a frame says why, as
  `frame_skipped`. Beside them, `run_start.decision_llm.frames`, `frames` and `frames_sent` in the
  summary's `decision` block, and `1 frame` on the turn's line in `quackd log`.

## Why not

- **Frames by default for a row that can see.** A picture of wherever the robot is leaves the
  machine on every turn the stepper is asked, which in shadow mode is every turn, and that is
  never a default.
- **Frames following `--vision`.** That flag is the pilot's, it is on by default for a cloud pilot,
  and a decision about a second vendor seeing the room must not ride on it.
- **Every camera.** The primary is the view the detections and the bearings describe. More cameras
  are more of somebody's room in each request, for a question no measurement has asked yet.
- **The PNG the transcript keeps.** Lossless and at the camera's own size, so as large as that
  camera makes it, every byte sent inside a one-second turn. A JPEG no longer than 512 pixels on
  its longest side puts a bound on what one turn carries whatever the camera is.
- **Frames to a server of your own, loopback included.** It would let the path be tried without a
  key, but the rule is OpenAI's own hosts, and an address on this machine can be a tunnel to
  anywhere.
- **A new method, or a `frames` argument on every backend.** Every other row and every plugin would
  have to take an argument it must never be handed, and a plugin could then be handed one.
- **Letting a plugin say that it sees.** The gate would then be a plugin's own word.
- **The picture inside the state.** The state is measured, trimmed and estimated as text, and the
  record keeps its size. Bytes inside it would break all three and put the picture where a failed
  turn's reply could quote it back into the record.
- **A longer second for a turn with a frame.** One figure on a forum is not a measurement, and
  shadow mode and the live test come first.
- **Writing the copy in a worker thread.** An interrupt landing during the write could leave a file
  that no record names, or a record naming a frame no request carried. Encoding, the CPU work, stays
  off the loop.

## Consequences

- One row may see, on a run that says so. The stepper still cannot track: the repeat gate and the
  streak limit of ADR-0040 end any run of identical calls, so a head following a target is not
  this.
- [SECURITY.md](../../SECURITY.md)'s rule on pictures moves from never to this: only with the flag,
  only for a row that can see, only at OpenAI's own hosts, and the record keeping every frame sent.
  A picture reaching a decision LLM otherwise is still a security issue, and so are image bytes in
  the state or the record.
- OpenAI's data controls page, read 2026-10-08, says image inputs to this endpoint are scanned for
  child sexual abuse material when they are sent, and that one the classifier flags is kept for
  manual review even under zero data retention.
- What a frame costs in tokens and in time on `gpt-6-luna` is unpublished and unmeasured, and the
  `openai` page lists it as unverified. A turn that failed after its frame was handed over, and so
  has no count from OpenAI, is estimated on its text alone, which is low for such a turn.
- **Nothing here has answered a real robot, and no frame from quackd has reached OpenAI.** In CI
  the client runs against a fake of the SDK. Wherever the SDK is installed, a real `quackd run`
  sends its frames through it to a stand-in on 127.0.0.1 treated as OpenAI's, which checks each
  image part against the reference's shape, and those tests have run against 3.22.1 alone.
