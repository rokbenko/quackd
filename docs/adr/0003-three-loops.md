# ADR-0003: Three loops, three rates, three owners

**Status:** accepted, amended · **Date:** 2026-08-28 · Amended by [ADR-0024](0024-open-duck-mini.md) (0.5: on an Open Duck Mini v2, whose runtime has no network control API, quackd's own daemon hosts the reflex loop. It still writes none of the control code, and feeds that loop the same command vector a gamepad would, so "quackd never touches this" below should be read as "quackd never writes this")

**Amended 2026-09-28 by [ADR-0048](0048-policies-are-the-arms-executor.md):** the steering tier has a second kind of loop on the
SO-101 arm. A segment of the arm's learned policy, `pick` or `manipulate`, is a loop in quackd's
process, paced on the arm's clock at the policy's own rate, anywhere from 1 to 60 Hz rather
than the composites' 10 Hz. Like a composite it calls no model: the policy's inference runs in a
server process of its own, and the loop takes its goals and holds each to the arm's rules
before it is sent. The reflexes on the arm are still each servo's own position controller, and
the consequence below still holds: the model calls `manipulate` with one short subtask and
judges what the arm did from a fresh look, so a policy is something the LLM calls, not
something it is.

**Amended 2026-10-19 by [ADR-0054](0054-a-reflex-may-look-through-a-decision-llm.md):** "Composite
verbs never call the LLM" below reads "never call the *pilot*". The steering tier has a third kind
of loop on a Microduck, `watch`, which keeps a thing the pilot described in view with the head, a
look at most every half second, by asking a decision LLM that can see two closed questions about
each frame, where the thing is across it and how high, on a run started with `--decision-frames`.
What comes back is a sector, and quackd turns it into a move of its own, clamped to the head's
numbers, so the decision LLM decides where and never how. The pilot still calls it as a verb, and
nothing about it reaches the deliberation tier.

## Context

An LLM takes 1–10 s to answer. A biped falls over in 0.3 s. Upstream's own architecture
note says it plainly: "LLM latency means the agent is a *high-level* controller", and
`robotd` stops the robot itself if commands stall ("LLMs stall mid-inference").

## Decision

| Loop | Rate | Where | Owner |
|---|---|---|---|
| Reflexes | 50 Hz | onboard `robotd` | RL policies: balance, gait, stand-up. quackd never touches this. |
| Steering | 5–20 Hz | quackd process | perception + composite verbs (`walk_to` closes the approach loop from detections). |
| Deliberation | ~0.2–1 Hz | LLM | reads a frame summary + state, picks the next **verb**, judges success. |

The LLM decides **what**; the steering loop decides **how to get there**; the RL policies
keep the duck **upright**. Concretely:

- The LLM's only output is one tool call per turn (`quackd/agent/loop.py` enforces it).
- Composite verbs never call the LLM. `walk_to` runs a 10 Hz detect→steer loop in Python.
- Built-in verbs are intents (`robot.move`, `robot.do`), never joint targets.
- The transport owns time (`now()`/`sleep()`), so the steering loop runs at sim speed in the
  simulator and at real time on hardware without changing verb code.

## Consequences

- A slow or stalled LLM degrades the *task*, not the *safety*: the last verb finishes or
  times out, the duck stops, upstream's deadman stops it anyway.
- Perception must be cheap enough for 10 Hz on a laptop — hence a colour-blob detector by
  default (ADR-0008), not a VLM call per frame.
- Learned verbs (v2) slot into the steering tier, not the deliberation tier: they are things
  the LLM *calls*, not things it *is*.
