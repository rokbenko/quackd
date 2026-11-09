# ADR-0079: What rides on a body's back is a figure of its own, and an unset one is not on the wire

**Status:** proposed; accepting it is Rok's · **Date:** 2026-11-09 · Follows [ADR-0077](0077-a-body-quackd-only-describes-is-one-yaml-in-the-core.md), whose consequence held a quadruped's load back for a record of its own, and [ADR-0032](0032-datasheets-and-the-verdict.md) (a figure has a confidence and a source, and an unknown one is not a yes) · Implemented in `quackd/adapters/manifest.py` and `quackd/verdict.py` · Documented in [docs/reference/manifest-spec.md](../reference/manifest-spec.md#the-datasheet)

## Context

ADR-0077 wrote two makers' ratings for a load on a robot's back into notes, because the
datasheet's one carrying figure, `payload_kg`, is what a hand holds, and `_consistent` refuses
it with no manipulator. A note is something a pilot may read and nothing else can: a task cannot
ask for a back load in `needs`, no refusal can name one, and no list of bodies that could do a
task will offer such a body. ADR-0077 left it there on the ground that a `carries_kg` figure "is
a change to the manifest every peer reads", and the owner has now asked for one.

So the first thing is what a peer reads. The manifest never crosses between quackd processes as
a `RobotManifest`, and `Datasheet` is only ever built inside one process, from a body file, a
`robot.yaml`, an adapter's code or a task file's corrections, so `extra="forbid"` on it has never
judged anything a peer said. What crosses, traced through the code on 2026-11-09, is this:

- `digest()`, a hash of `model_dump(mode="json", exclude={"id", "backend"})`, in the mDNS record
  (`sha`) and in MCP's `robot_list` row, where a reader takes it as a fingerprint. One more key in
  that dump, even a null, changes the digest of every body quackd ships.
- A flock bid carries the datasheet as a `dict[str, Any]`, read by key through `missing_needs_in`.
  A key the reader has not heard of is read past. The flock bus is in-process by default, and the
  MQTT bus that fills the same protocol is library-only (`mqtt_bus.py`), but a bid is shaped for a
  stranger to read, so this is the shape that counts.
- MCP's `robot_list` gives a client the same dump as JSON, for an LLM, and no quackd reads it back.
- A role's `needs` travels inside TASK as `FlockTask.roles`, and `FlockRole` runs `check_needs` on
  decode. A word the reader's vocabulary lacks fails that decode, and `MqttBus._on_message` counts
  the message as rejected and drops it.
- A passport is another document, with its own normative list of `body` members (spec section 2.3)
  and its own rule for a member it does not know (rule 8: ignored, with a note).
- A `.duck` file is read by whichever release loads it, with `extra="forbid"` on its `datasheet:`
  block and `check_needs` on its `needs`.

So a design owes two things. A body that says nothing about its back must look, on every one of
those, exactly as it looked before. And a body that says something must be read past by a reader
that cannot use it, and refused only where a reader is asked to act on what it cannot read.

## Decision

**A datasheet gains one optional figure, `carries_kg`: what rides on the body's back or deck as
it moves, with no hand holding it, in kilograms, a `Figure` with a confidence and a source like
every other, and a key in a dump only where a body says it.**

It is not `payload_kg`. A payload is held by a hand, the beak or both arms. A back load needs no
hand and no manipulator, so `manipulator: none` allows it, and `_consistent`'s refusal of a
payload or a reach with nothing to hold with is untouched.

It holds the number a task that moves the body can be held to. Where a maker separates a walking
figure from a standing one, the field holds the walking figure, and the standing one goes in the
figure's `note`. Where a maker gives a nominal figure and a maximum, it holds the nominal, and the
maximum goes in the `note`. That is ADR-0077's rule that the lower end of a range is the figure,
and a walking figure is the lower end for a reason: a body standing still can bear what it cannot
walk under. A pilot reads both numbers, because the `note` is rendered with the figure, and `needs`
reads only the one the field holds.

`needs: {carries_kg: 10}` is a minimum in `NEEDS_NUMBERS`, read by `missing_needs_in` like the
others: a body that declares 8 is `carries_kg >= 10 (has 8)`, a body that declares 10 or more
meets it, and a body that says nothing is `(not published)`, refused by default in the verdict
gate, the matcher, a role and the coordinator alike. The kindness `own_sheet` shows a working
height is not shown here: a working height can be judged against a body's height and reach, and
nothing else on a sheet speaks to what a back will bear. `payload_kg`'s own text now says what a
hand holds, so a pilot has one word for each.

The prompt shows the figure where a body says it, as `Load on its back or deck`, and never lists
it as a gap where a body does not, which is `workspace_height_m`'s standing and for the same
reason: a figure most bodies could only ever leave empty would be noise in every prompt, and a
task that does turn on it is told by the gate. A task file can state it in its `datasheet:` block
for the build in front of it, which is how a person supplies the figure for a body whose maker
said nothing.

The field is left out of every dump where unset, by a serializer on `Datasheet`, and is an
ordinary field everywhere else. That is the whole of compatibility for a body with no such
figure: its digest, its bid and its `robot_list` row carry no new key, so a peer on an older
release sees byte for byte what it saw. A body that says it adds one key to the bid's free-form
datasheet and a new digest to itself, and an older coordinator reads past the key, while an older
member's own bid, with no key, reads as a body that said nothing. A role or a task file that
names `carries_kg` is refused by an older quackd in its own words, at load or, over the bus, as a
TASK it cannot read. Refusing is right: a member that cannot say what the word means is not one
to bid for a role that turns on it.

The passport does not carry it. A passport's `body` is what its specification lists, normatively,
with a schema and conformance vectors beside it, and a new member is a minor of that
specification rather than a side effect of this one. Until then `quackd/adapters/passport.py` and
`quackd/onboard/body.py` keep their `_FIGURES`, `quackd/onboard/config.py`'s `Body` refuses the key
as one `robot.yaml` does not know, and `quackd/passport/document.py` reads no such member.

## Consequences

- Every body that says nothing keeps its digest: the goldens in
  `tests/golden/manifest_digests.json` and `tests/test_arm_types.py` hold, and comparing all 38
  static manifests quackd ships before and after this change found 36 identical. The two that
  differ are the B2 and the Go2 EDU, because their sheets changed, as any edit of a body's figures
  changes its digest, and neither travels on the LAN: `quackd announce` refuses a described body.
- The Unitree B2 and the Go2 EDU move out of their notes into the field, read again on 2026-11-09:
  40 kg on the B2, from `unitree.com/b2`'s "Load(Walking)" row of "> 40kg", with its "Load(Standing)"
  row of "≥ 120kg" in the note, and 8 kg on the Go2 EDU, from `unitree.com/go2`'s Payload row, "≈8kg"
  in the EDU column, with its "MAX ~ 12kg" in the note.
- A `robot.yaml` cannot say a back load and a passport cannot carry one. The day the specification
  names such a member, every place the passport and onboard code spells `payload_kg` gains a line
  beside it.
- `docs/adapters/status.md` gains a Back load column, generated like the others, so the two figures
  are on the page a reader checks.
- `manifest.schema.json` gains the property. A checker holding an older copy of it refuses a sheet
  that has the figure, since that schema is `additionalProperties: false`, and accepts one that
  does not.
- A task file or a role that names `carries_kg` needs this release or a later one, and an older
  quackd refuses it. That is the cost of `Added`, a minor, in RELEASING.md's table.
- The walking rule is only as good as the page. A maker that publishes one number with no word for
  whether it walks gets that number, and the note says what the page said.
