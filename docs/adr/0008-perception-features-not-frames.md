# ADR-0008: Features, not frames — a colour-blob detector by default

**Status:** accepted, amended · **Date:** 2026-08-28

**Amended 2026-09-25 by [ADR-0046](0046-the-jetson-is-reached-not-run-on.md):** a `Detector`
whose model runs on another machine arrived before `mediad`'s feature stream did, and it is not
that stream. `HostDetector` (`quackd/perception/host.py`) still takes the image. It sends the
primary frame to the board `--host` names as one JPEG and turns the boxes it gets back into
detections with the geometry `YoloDetector` uses, so the protocol is unchanged. The `Detector`
that reads a socket instead of an image, which *Consequences* below anticipates, is still
unbuilt. `HostDetector` waits on the network, so quackd runs it in a worker thread, and a
steering verb keeps re-sending its last twist while the board answers, for one deadman window
and no longer.

**Amended 2026-10-19 by [ADR-0054](0054-a-reflex-may-look-through-a-decision-llm.md):** a decision
LLM may be shown the primary frame inside the Microduck's `watch` verb, as it may be shown one
beside the stepper's state by [ADR-0053](0053-a-decision-llm-may-be-shown-the-camera.md), and only
on a run started with `--decision-frames`, at OpenAI's own hosts. What it answers is a sector of the
picture, never a `Detection`: no bearing, no distance and nothing the steering loop reads. The
`Detector` is still the only perception in the 10 Hz loop, and the pilot still reads features.

## Context

Upstream's principle: put perception next to the sensor and publish *features* ("ball at
(x, y)", "person detected"), tens of bytes at 10–30 Hz. The steering loop needs detections
at ~10 Hz on a laptop; the LLM needs a sentence, not a pixel grid, to decide the next verb.

## Decision

- `Detector` protocol → `[Detection(label, cx, cy, area, confidence, bearing_deg,
  est_distance_m)]`. Verbs and prompts consume only this.
- Default `ColorBlobDetector` (OpenCV HSV threshold, ~1 ms/frame, zero downloads).
  The sim draws the ball in a known orange (H≈16 in OpenCV's 0–180 scale), the person in
  blue, a pet in green. Geometry: `bearing = -atan((cx - w/2) / f)` (positive = left,
  upstream's +yaw convention); `distance = f · r / radius_px` for round targets and
  `f · half_width / (w_px/2)` for upright ones, with `f = (w/2) / tan(FOV/2)`.
- `YoloDetector` (`quackd[yolo]`, lazy `ultralytics` import) maps COCO classes to the same
  labels for real cameras. Not needed for the demo.
- The LLM sees a summary line (`ball at bearing 12° left, ~0.80 m`) plus, for vision
  providers, the last frame or two as images; older images are dropped from history.

## Consequences

- Tuning for a real orange ball = one `HSVRange` (documented in `docs/faq.md`); a real
  IMX219 = `fov_deg=62`. Nothing else changes.
- Distance from apparent size is crude (±20 %) but monotonic, which is all `walk_to`
  needs; the stop condition is verified by the kick result or a fresh frame, not trusted.
- When upstream ships `mediad`'s feature stream, it becomes one more `Detector` that reads
  a socket instead of an image.

## Note (2026-09-16): several views, still one detector

A LeRobot arm can be registered with more than one camera (`--camera-url` repeats), so "the last
frame or two as images" in *Decision* is now the last frame or two from every camera it has. They
go out in one message, each picture preceded by a text part naming its camera, because two
unlabelled views of a desk say nothing about which lens took which. The saved frames carry the
name too (`frames/0007-top.png` where a single camera writes `frames/0007.png`).

This does not change the decision, and the three things it rests on are untouched:

- The pilot still reads features. The summary line is still the designed path, and a text-only
  model still gets the whole task through it.
- The `Detector` still runs on one camera, the first url, the one `--fov-deg` describes. A
  bearing is only meaningful from the lens it was measured on, so when that camera is the one
  that stalls the line reports nothing seen even though the other views still reach the model.
- The verbs that steer by sight still read that one camera at ~10 Hz, for the reason this ADR
  was written: fetching every camera inside that loop would spend the deadman window on pixels.

The cost it does add is pictures per request. The last two exchanges keep their images and that
limit counts exchanges rather than images, so two cameras is four pictures in a request rather
than two.

## Note (2026-09-18): a picture that comes with the task is not perception

`quackd run --image sketch.png` hands a run a picture of something that is not in front of the
robot: a drawing to copy, a photograph of the shelf as it should end up, a diagram of the knot.
The flag repeats, and the pictures ride on the first observation and are never trimmed out of
the history, so a task about a picture still is one twenty turns later.

None of that is perception, and the word is worth keeping clean. These pictures never reach the
`Detector`. They produce no `Detection`, no bearing, no distance and no summary line. They are
not what the robot can see, they are what the person asking already had, and the prompt says so
in a section of its own (`## The picture(s) that came with this task`) rather than mixing them
into the camera's. In the request they are labelled `task picture <name>:` and sent first, and
the camera frames that follow are named whenever a task picture is present, because the one
failure worth designing against is a model that answers about the drawing when it was asked
about the desk.

So the three things above are untouched. The pilot still reads features, and the summary line is
still the designed path. The `Detector` still runs on one camera, the first url, the one
`--fov-deg` describes. The steering verbs still read that one camera at ~10 Hz and spend the
deadman window on nothing else.

The cost is the same kind as the note above, and smaller. A task picture is one more picture per
request for the whole run rather than one more per step: the count is fixed when the run starts,
it does not grow, and it does not ride on the two-exchange image window, because it is attached
once and then simply never trimmed. A pilot whose model takes no images is refused the flag
before the run starts rather than handed a task about a picture it will never see.
