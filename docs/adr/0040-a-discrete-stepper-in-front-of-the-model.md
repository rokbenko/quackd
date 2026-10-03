# ADR-0040: A discrete stepper in front of the model

**Status:** accepted, amended · **Date:** 2026-09-18 · Extends [ADR-0010](0010-providers.md) (what a provider is, and what quackd asks of one) and [ADR-0032](0032-datasheets-and-the-verdict.md) (the feasibility verdict and why it is prose) · Implemented in `quackd/agent/decision/` and one hook in `quackd/agent/loop.py` ([page](../guides/decision-llms/README.md))

**Amended 2026-09-22 by [ADR-0043](0043-decision-llms-are-a-wire-format-and-a-data-row.md):** Jev turned out to be the first of many, so everything below
that names the vendor now names the format. `--jev off|shadow|on` is `--decision-llm NAME`,
`--decision-mode off|shadow|on` and `--decision-url`; `quackd[jev]` is `quackd[decision]` (and
`quackd[laya]` for the one that runs in this process); `QUACKD_JEV` is `QUACKD_DECISION_LLM`
plus `QUACKD_DECISION_MODE`; the two transcript kinds `jev` and `jev_shadow` are `decision` and
`decision_shadow`; `Source`'s fourth word is `decision`, so the log says `from decision`; the
summary block is `decision`; and `quackd/agent/jev.py` is the package `quackd/agent/decision/`.
Read "TypeSafe's own published numbers" about the floors as true of two of the four and not
of all of them: their confidence page publishes 0.5 and 0.9, which are the brake and the
confirm-gated floors here, while the read floor at 0.60 and the motion floor at 0.85 are
quackd's own, set between those two. All four are shaped around Jev and inherited unmeasured
by every other one. The reasoning below is unchanged, which is why this is an
amendment and not a replacement: what turns are a choice, what an answer must clear before it
moves a servo, and what a stepper is never allowed to do are quackd's half and do not move with
the vendor.

**Amended 2026-09-28 by [ADR-0048](0048-policies-are-the-arms-executor.md):** the classification below stays computed, with one
exception written by hand. `manipulate` narrowed to a task file's instructions is a choice by
the rule, one label per instruction, and the stepper is offered it and never takes it, in `on`
as in shadow (`SHADOW_ONLY` in `stepper.py`). It is confirm-gated, and under `--yes` nobody is
asked at that gate, so a stepper that cleared the confirm floor would start a learned policy
driving the arm with no person and no model involved. An answer that clears every other gate
ends on `gate: shadow_only`, the model takes the turn, and a turn that offered it writes a
`decision_shadow` record in `on` mode as in shadow, which is the agreement rate a decision to
promote it would need.

## Context

quackd's loop asks one question a turn — which single tool call now — and pays a frontier
model's full latency for it whether the answer is `report_state` or a six-joint pose. On the
SO-101 run at the top of `README.md` that is 62.1 seconds of a 78.8 second run spent waiting on
the model, against 12.2 seconds of the arm moving. It is the single largest cost in a quackd run
and it has been since there was a loop.

Not all of those turns are the same kind of question. `move_joints` with three angles is an act
of authorship: the model reads a datasheet, picks numbers inside a travel limit, and writes
them. `report_state` is a choice among five things the arm can do, and so is `stop`, and so is
shutting the gripper. A frontier model answers the second kind perfectly well, and pays the same
six seconds to do it.

[TypeSafe's Jev](https://docs.typesafe.ai/introduction) is a *System One* model: typed questions
evaluated against a named state, returning a value and a probability distribution, with no text
generation anywhere in it. It answers the second kind of question and cannot answer the first,
which is the property this decision rests on.

The risk is obvious and it is the reason this repository exists. quackd's whole argument is that
a language model names a skill and never writes a motor command
([ADR-0011](0011-hardware-verb-mapping.md)). Putting a cheaper, smaller model anywhere near the
decision loop is only defensible if there is a line it structurally cannot cross.

## Decision

- **Optional, off by default, and off when a key is lying around.** `quackd[jev]` is its own
  extra, is not in `quackd[all]`, and `typesafe-sdk` is not in `dev`, so the default test suite
  has nothing to import even by accident. `--jev off` is the default and `QUACKD_JEV` is unset
  is the default; a `TYPESAFE_API_KEY` in a `.env` file switches nothing on. On the off path no
  stepper is constructed and `typesafe_sdk` is never imported.

- **It is not a provider.** It generates nothing, so it cannot pilot a robot, and `--provider`
  does not take it, `PROVIDER_NAMES` does not list it and `quackd doctor` gives it a section of
  its own rather than a row in the providers table. The concrete reason for the separate list is
  `cloud_keys`: a machine with no TypeSafe key is not a machine with a problem.

- **What it may answer is decided from each tool's JSON Schema and nothing else.** A verb is a
  choice when every parameter is a closed set — an enum, a constant or a boolean — or is inert,
  meaning optional, defaulting to null and accepting null, so omitting it chooses nothing.
  Everything else is a number, and a number is the model's. The rule is computed, not curated,
  so a body quackd has never shipped is classified by the same sentence as the seven that ship,
  and a frozen table test makes a new verb say which side it falls on.

  The exemption is the hinge and is worth spelling out. `gaze(direction="left")` has a nullable
  `bearing_deg` that defaults to null: omitted, it has no value and the verb does what its enum
  says. `move()` has `vx` defaulting to 0.15, so omitting it walks the robot at 0.15 m/s, a
  speed chosen by not choosing. `default: null` against `default: 0.15` is in the schema, so no
  verb needs judging by hand.

- **`move_joints` is never a choice, on any body, for two independent reasons.** Its `positions`
  is a required object, which the rule rejects. And the joint names are not in the schema at
  all — they live in a `field_validator` — so there is nothing for a classifier to enumerate
  even in principle. This is the sentence the whole feature rests on.

- **No meta tool is a choice, and that is a safety property rather than an accident.**
  `assess_task`, `declare_success`, `declare_failure`, `remember` and `tell` all take a required
  free string. So the stepper cannot end a run, cannot record a feasibility verdict, cannot
  write to memory and cannot speak to a flock. Every ending goes through the model or through a
  budget.

- **A turn the stepper answers appends nothing to the model's history.** Not an exchange with no
  decision — nothing at all. `self.history` is what the model has seen, and that is now true
  rather than nearly true. The model is told what happened in one line on the observation it is
  next shown, naming the verbs and saying who chose them.

- **It is offered only what the executor would run this turn.** The label set is computed from
  the allowlist, the verb's safety class and `executor.cleared`, using the same two conditions
  the verdict gate itself uses. Before a verdict that is the reading verbs and the brake. So
  `VerdictRequired` and `VerbNotAllowed` are unreachable from a stepper-authored call rather
  than caught.

- **Confidence floors are TypeSafe's own published numbers, by what the verb does**: 0.50 for
  `stop`, 0.60 for a read, 0.85 for anything that sends an intent, 0.90 for a confirm-gated
  verb, and a `dangerous` verb is never offered at all. `stop` sits at the lowest floor in the
  system on purpose: a wrong `stop` costs one step and a wrong anything-else costs a move nobody
  chose.

- **A run always reaches the model.** The stepper never answers twice running with the same
  call, and never more than eight turns in a row. Both hand the turn back and start counting
  again.

- **`--jev shadow` before `--jev on`.** Shadow asks on every turn, records the answer beside the
  model's, and changes nothing about the run, so it is safe on hardware and it is how any claim
  about speed or agreement gets earned.

## Why not

- **A `SteppedProvider` wrapping `cfg.provider`.** This is the shape that presents itself: zero
  diff to the loop, the stepper returns a synthetic `ProviderTurn`, everything downstream is
  unchanged. It is wrong four ways and all four matter. The loop writes a `Decision`
  unconditionally, so the stepper's call would be replayed to the model as its own words — and
  on Gemini as a `function_call` with no `thought_signature`, which that model refuses the next
  turn over. It would charge `max_llm_calls` for a turn that made no model call. The `llm` trace
  record would claim the model answered, with a fabricated `usage`. And a provider cannot see
  `executor.cleared`, so it could not avoid offering a verb the verdict gate is about to refuse.

- **Recording the stepper's turn as an exchange with `decision=None`.** Wire-safe, and still
  wrong. Every observation ends `Choose exactly one tool.`, so a history containing turns where
  the model was told that and produced nothing is in-context training against the one invariant
  the loop enforces. The model would keep paying for observations it never saw, which inverts
  the cost argument the feature exists for. And `keep_images_for_last_n` counts exchanges, so
  two stepper turns in a row would push both of the arm's camera frames out of context — the
  model going blind precisely because something else was driving.

- **Letting it write the feasibility verdict.** `feasible|infeasible|uncertain` is a textbook
  Choice and it is asked on every turn, but only recorded. `assess_task` needs a written reason,
  and it needs `needs`, which `own_sheet_objection` reads to refuse a `feasible` verdict naming
  a figure the datasheet does not publish ([ADR-0032](0032-datasheets-and-the-verdict.md)). A
  verdict with an empty `needs` passes that guard trivially and silently disables it. And an
  `uncertain` verdict puts its reason to the person in the room; an empty question is not a
  question. The version this argues for is a stepper that *checks* the model's verdict, not one
  that writes it, and the recorded Choice is how that case gets made or dropped.

- **`--jev` on `record` or on `serve-mcp`.** `quackd record` makes the recordings in this
  repository and has to stay reproducible without a network call. Over MCP the model is the
  client, so quackd has no think path to put anything in front of.

- **Evaluating preconditions in the stepper.** It would stop the stepper choosing `place` on an
  arm holding nothing. It would also be a second copy of a judgement the executor already makes,
  in a different place, able to disagree with it. The executor is the one authority on whether a
  verb may run; a refusal comes back as a result the next turn can read. The verdict gate is the
  exception because it is contract state rather than physical state, and because it is the one
  that would otherwise fire on almost every early turn.

## Consequences

The loop grows one branch and the off path is the code that was there before, reindented.
`Source` grows a fourth word, so `verb_start` says `from jev` with no renderer change. `Budget`
grows a counter that bounds nothing, so `status()` can say where the turns went, and the string
is byte-identical until the stepper has answered once, which matters because it appears in every
observation and `quackd trace` parses it back out.

Two trace kinds, and `summary.json` grows a `jev` block only when there was a stepper. A
transcript written before this replays unchanged, and a transcript written after it read by an
older quackd degrades quietly, because `render_events` returns nothing for a kind it does not
know.

**Nothing here has run against a real robot.** It is exercised against the mock arm, the
simulators and the suite, with a stub standing in for TypeSafe, which is the same standing the
rest pose and `--by-hand` have. The speed and cost figures in `docs/jev.md` are an estimate,
built from TypeSafe's published 0.114 s and $0.042 per million input tokens and from quackd's
own measured timings, and they are labelled as one. The run-level saving is governed by how
many of a task's turns are a choice rather than by the per-decision ratio, which is why that
page gives a table over that fraction rather than a single multiplier.
