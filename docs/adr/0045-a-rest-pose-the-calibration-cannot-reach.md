# ADR-0045: A rest pose the calibration cannot reach

**Status:** accepted, amended · **Date:** 2026-09-23 · Extends [ADR-0036](0036-what-the-arm-does-not-say.md) (the range refusal, and `stop` as a hold of the five body joints) and [ADR-0039](0039-an-arm-placed-by-hand.md) (the one place quackd lets go, on the condition `close()` trusts) · Implemented in `adapters/lerobot/` (`verbs.py`, `real.py`, `mock.py`), `quackd/adapters/base.py`, `quackd/agent/loop.py` and `quackd/doctor.py` ([page](../adapters/lerobot/README.md#a-pose-past-the-travel), [first run](../adapters/lerobot/first-run.md#07-record-the-rest-pose))

**Amended 2026-09-23, by `quackd robot release`:** the reachable rest pose is no longer the only
place quackd releases an arm. A person at the arm can ask for its torque by name, wherever it
stands: `quackd robot release NAME`, or Enter at the offer a run makes at its terminal when its
last rest move missed, which is the genuine miss this ADR keeps torque on for. The decision
below is unchanged for everything quackd does on its own initiative. A genuine miss still keeps
torque on, and taking it off is now a person's call, made after being told to hold the arm,
where before the only call left to them was the power switch. The line a genuine miss ends on,
`TORQUE_LEFT_ON`, names the ways out with the name the arm was registered under, and puts the
hold before all of them, because both commands connect and connecting takes torque off every
motor for a moment: hold the arm first, then run `quackd robot release`, or run
`quackd doctor --robot` to try the rest move again, or cut its power. "It will not fall" is said
of the arm as it stands. [ADR-0039](0039-an-arm-placed-by-hand.md)'s second amendment has the
rest.

**Amended 2026-09-24:** `take_hold()` no longer skips a joint placed past its travel. It refuses
before it writes anything, torque stays off and the arm stays in the person's hands, because the
goal a skipped joint keeps after a hand-off is the rest move's and not the limit. The decision
on the take-hold below says why, and what the teardown then does, which is to leave the arm
alone: nothing takes hold of it again, nothing writes it a goal, and nothing folds it, whatever
the person does with the joint afterwards ([ADR-0039](0039-an-arm-placed-by-hand.md)'s amendment
of the same day has the rule for every refused take-hold).

**Amended 2026-09-27 by [ADR-0047](0047-the-arms-simulator-runs-the-real-backend.md):** the
arm's simulator reproduces the servo behaviour this ADR rests on, from the arm's own
calibration file: a goal past the travel is clamped to it without a word and a reading is not.
So wherever the model's own stop lies beyond the calibrated travel, a rest pose recorded past
the travel parks at its edge or is found already folded past it, as on the arm, and a
`--by-hand` take-hold meets whatever pose gravity left the released arm in and refuses a joint
past its travel in the same words. What it cannot reproduce is a fold past the model's own
stops, which truncate the pose the simulated arm starts in, with a connect note, until the bench
reads the real stops against the model's. Nothing in the decision below changes.

## Context


The rest pose was written after the bench of 2026-09-15, where the arm fell at the end of every
run, and it met an arm for the first time on 2026-09-23: the same SO-101, `arm-01`, on lerobot
0.6.1 and Windows, with a pose recorded under its registered name. Nineteen of that
afternoon's 26 runs never moved the arm at a pilot's request, most of them for one of five
separate reasons, and the rest pose was the first of them.

The recorded pose had `shoulder_lift` at -104.7. The travel `joint_ranges()` read off the
arm's calibration, written on 2026-09-15, was ±84.2 for that joint: `range_min` 1180 and
`range_max` 3096, a midpoint of 2138, and the fold at tick 947, 233 ticks or 20.5 degrees
below the floor. That calibration had never seen the shoulder folded all the way back. The fold
was where a hand put the arm afterwards.

The rest move sent the recorded pose unclipped, on purpose. A pose read off the arm was where
the arm had physically been, a folded arm often sits outside its recorded travel, and the range
refusal that guards `move_joints` would have refused to put it down; the adapter page said so,
with an earlier reading of -113.5 against ±84.2 as its example. That rested on the servo
following a goal past the travel, and nothing had checked that it would. It does not.
LeRobot's `write_calibration()` writes each motor's `range_min` and `range_max` into its
EEPROM as `Min_Position_Limit` and `Max_Position_Limit` (`motors/feetech/feetech.py` lines 268
to 276 at 0.6.1). `_unnormalize` bounds nothing in DEGREES mode (`motors/motors_bus.py` 904 to
907), so the tick it computes goes to the servo as it is. And the STS3215 clamps
`Goal_Position` to its two limits. The runs show it from both sides. Driven down from above,
the shoulder stopped at tick 1181, one inside its floor. Starting from the fold, every goal
written past the floor, the reading itself included, moved the arm **up**, to between -86 and
-88 degrees by every reading taken once it had stopped: the limit, less what a P-only
controller sags under the arm's weight.

What followed, all of it in that afternoon's traces:

- The rest move's stall check (`STALL_TICKS`) fired 17 to 20 degrees short of the recorded
  angle, so every run that did not start within `TOL_DEG` of the recorded pose aborted with
  `the arm did not reach its rest pose` before its first model call. Six runs ended that way:
  four whose shoulder started above the fold, and two whose shoulder started further into it
  than the recorded angle, where the clamp moved it up past that angle just the same. The other
  sixteen that reached the rest move started at the fold and were `already at the rest pose`.
- Every run that reached its close, 21 of them, ended on `TORQUE_LEFT_ON`. The six above missed
  the same move again at their end. Of the fifteen that got past their start, six ran the move
  at their end and missed it at the limit, and nine found the shoulder still folded and
  `already at the rest pose` a moment after the teardown's `stop` had set it rising, so that
  `close()`, 0.3 to 0.6 s after that stop, read it at the limit (the next point). Every one of
  those runs ended at the power switch.
- `_hold()`, which every `stop` and every teardown reaches, wrote each body joint's present
  position as its goal, unclipped. For a shoulder folded past its floor the servo clamped that
  goal to the limit and drove there at full speed. In one run the final state, recorded 15 ms
  after the run's last stop, read the shoulder at -107.6, and the close note, 0.34 s after that
  stop, read it at -87, with no clamp warning from LeRobot in between because the goal quackd
  wrote was the reading itself, well inside the step cap. The stop was what moved it.
- Three pilots shown `shoulder_lift` past its travel, at -104, -102 and -108, beside a prompt
  line giving that travel as -84.2 to 84.2, with nothing to reconcile the two, gave it as a
  reason not to move an arm in a state they could not explain.
- Holding at about -87 against a goal clamped at -84.2 kept the shoulder's servo pushing
  whenever torque was on. Its temperature, as quackd reads it off the bus, was 34 °C and 35 °C
  on the first three runs of the afternoon and 37 °C to 40 °C on the runs after them, apart
  from one reading of 45 °C at the end of the second to last run and a single reading of 67 °C
  that the next one, 0.17 s later, put back at 37 °C.

Those numbers are one arm's, on one calibration, and they are here as the record of what
happened. Nothing below uses them. Every figure the code works with is read off the arm it is
driving, and the tests build calibrations and poses of their own.

## Decision

**The rest move drives to the reachable pose.** `verbs.reachable_rest_goal(rest_pose, ranges)`
clips each body joint of the recorded pose into the travel `joint_ranges()` read off this arm
at connect: either end, any number of joints at once. It returns the clipped joints as
`(joint, recorded, reachable)`. A joint with no known range passes through, the gripper is
never in it because it is never driven, and `wrist_roll`, whose calibration is a full turn, is
never clipped in practice. `rest_pose` stays the pose as recorded. The reachable pose is
computed from it and the calibration each time it is asked for (`LeRobotReal.rest_reachable`),
so it cannot outlive the calibration it came from.

**"At rest" is a half-line for a clipped joint.**
`verbs.joint_at_rest(goal, reading, recorded)`: where the recorded angle lies below the floor,
the joint is at rest at `reading <= goal + TOL_DEG`, and where it lies above the ceiling, at
`reading >= goal - TOL_DEG`. The side is read off the sign of `recorded - goal`, so a fold past
the ceiling is the mirror of one past the floor. Every other joint keeps the band of `TOL_DEG`
(5.0 degrees) either side of its recorded angle, and `at_rest` called with two arguments judges
exactly as it always did. The reason is the servo's. Nothing quackd can write drives a joint
further past its limit than the few degrees a loaded joint sags there, so a joint that reads
well past it was put there with torque off, by a hand or by its own weight settling: it is
folded, not lost. A joint parked at the limit and sagging past it is at rest by the same rule.
`go_to_rest`, `close()`, `let_go()` and the mock all use the rule, so an arm parked at the edge
of its travel has `arrived`, an arm already folded past it is `already` there, and both are let
go of. A joint that stops short *inside* its travel, against a hand or the desk, is still a
miss, and it still keeps torque on with `TORQUE_LEFT_ON`.

**Torque is released at the reachable pose, and the joint is left to settle the rest of the way
on its own.** This was decided at the bench, against the alternative, which was an arm that
keeps torque on at every close until somebody recalibrates it: that alternative is the
afternoon above. It is safe enough to do because the joint is let go at the nearest point to
its fold that the servo can reach, on the way to a fold the person watched the arm hold limp
when they recorded it, and because the note says which joint, where it was recorded and where
it parks, so the distance it may travel is in front of whoever is standing there.
`verbs.worth_saying` names only the joints clipped by more than `TOL_DEG`: a smaller clip is
inside the tolerance any reached pose may miss by, and a sentence about it would be a
sentence about nothing.

**The rest move never sends the limit to a joint already past it.** Each tick of
`_drive_to_rest` sends the reachable goal less any clipped joint that already reads beyond it
on its fold's side (`verbs.past_reach`). The goal that joint would get is the limit, and sending
it hauls the joint up out of its fold. Such a joint is at rest by the half-line, so leaving it
out never keeps the move from arriving. The move's budget is sized on the joints that are left,
and `shortfall` is handed the recorded pose, so a genuine miss never names the folded joint as
the one that fell short.

**A hold writes no goal for a joint that reads outside its travel.** `_hold()`, which is every
`stop` and every teardown, leaves out any joint reading strictly outside its travel
(`_outside_travel`). Written to that joint, "stay where you are" arrives as "go to the limit"
once the servo has clamped it, which is what hauled the shoulder up. What it costs is that the
joint keeps whatever goal its servo already holds instead of a fresh one, and any goal quackd
writes to a joint while it reads past its travel is the limit to the servo: a step from a
reading past the travel is still past it, and the servo clamps it. So the skip avoids starting
a rise out of a fold and cannot halt one already under way. A joint a move had begun lifting
keeps rising to its limit at the servo's own speed whatever a stop writes or leaves out, and
the power switch is the only stop for that stretch. (This paragraph first said the joint's goal
was within a step of where it stood, which is true only inside the travel.) After a fresh
connect the goal is whatever the servo holds once torque comes back on, which is the question
`TORQUE_ENABLE_HOLDS_PRESENT` asks and nothing has answered. If every body joint reads outside
its travel, nothing is sent and `stop_error` stays None: the stop started nothing, which is all
it could do. The joints the hold left out are kept on the transport (`stop_skipped`), and the
core `stop` verb names them and says they read past their travel.

**A take-hold refuses a joint placed outside its travel, before it writes anything.** In
`take_hold()` a skip would not keep the limit. The last goal quackd wrote to that joint is the
rest move's, written while the joint sat inside its travel and before the person lifted the arm,
and a person can place the joint past the far end of the travel from it, so the goal the servo
keeps can be nearly the whole travel away. A goal written where the joint is would be clamped to
the near limit and haul it there, and no goal at all leaves the servo that stale one, which it
drives to on re-enable if `TORQUE_ENABLE_HOLDS_PRESENT` goes the wrong way. Neither keeps the
joint where it was put. So `take_hold()` refuses whenever a body joint reads outside its travel,
before any goal or torque write: torque stays off, `_in_hand` stays set, and the refusal
(`verbs.placed_past_travel`) names each such joint, its reading and its travel, says that a goal
written where the joint is lies past its travel and the servo would pull the joint to the end of
it, and says the arm is taken hold of only with the joint inside. A `--by-hand` run ends there
and tells the person at once that the arm is still in their hands, or, where the take-hold's own
read finds the whole arm at its rest pose with every motor off, a fold recorded past the travel
that nobody lifted before pressing Enter, that it is still limp at its rest pose and taken hold
of only once the folded joint is lifted inside its travel (`verbs.unlifted_from_rest`,
`HandResult.resting`). The same refusal met by the stop that opens the teardown of a Ctrl-C in
the placement wait is said to the person once and recorded, in the same words. After that
nothing in the run touches the arm: the stop takes no second hold and sends nothing
(`_refused_hold`), the hand-back is not asked, the rest move reads the arm and writes it nothing
(`IN_HAND_NOT_MOVED`), and the run says once that the arm is in their hands and not folded, or,
where that read finds the arm still at its rest pose, that it is already there. No release is
offered over it either. The close ends on the note for an arm in somebody's hands, let go of for
them to place, or, where its own read finds the arm still at its rest pose with every motor off,
which is a fold recorded past the travel that nobody lifted the arm out of, whether the
placement wait ended on Enter, on a Ctrl-C or unanswered, on the note for an arm limp at its
rest pose (`LIMP_AT_REST`). The gripper is not in the check, because LeRobot bounds a gripper
reading into its 0..100 range before quackd sees it. (This decision first said `take_hold()`
left such a joint out of both its writes, and that the goal it kept was the limit. That holds
for a goal written while the joint read past its travel and not for the rest move's, and the
skip it justified could turn a haul of a few degrees into a swing across the travel with a hand
on the arm. It then said the teardown's stop "meets the same refusal", which it did only while
the joint still read past its travel: a person who moved it back inside, as the refusal asked,
had the stop, or the stall of a rest move writing goals into the limp servos, take hold of the
arm under their hand with nothing said, and the run closed on torque left on. The refusal's own
sentence said the servo pulls "any goal written for that joint" to the end of its travel, which
is true only of a goal written past the travel.)

**The note travels on the rest result, never on `close_note`.** `RestResult` gains `clipped`
and `note`, both with defaults, so the six other bodies, which only ever build
`RestResult.none()`, and every equality between results are unchanged. `go_to_rest` sets
`clipped`, the joints clipped by more than `TOL_DEG`, on every result whether the pose was
reached or not, because it is a fact about the pose, and `note` only when the pose was reached.
The run says the note once, right after its first `at the rest pose`, because the rest move
runs at both ends and the note is about the pose rather than the move. `doctor` prints it as
advice under its table with the `rest pose` row green. The MCP session logs it when it parks at
connect. `quackd robot rest-pose` prints it as a warning before it asks, through
`LeRobotAdapter.rest_pose_note()`, so the command neither knows the rule nor reimplements it,
and it still records the pose. It is not a `close_note`, because everything that reads a close
note reads it as torque left on: `doctor` fails its verdict on any close note, and
`robot list --probe` shortens one to `torque left on: not at its rest pose`. A release that
said anything there would be reported as the opposite of what it was.

**The record and the pilot are told.** The manifest carries `extras.rest_pose_clipped` beside
`joint_range_deg`, and only when something was clipped, so a run's `run_start` names the clip
and the manifest of every other arm, its digest included, is what it was. `report_state` adds
one clause per joint reading past its travel, in the arm's own numbers, and the travel line of
the system prompt adds that a joint can read past its travel when it was folded or placed there
with torque off. Neither claims how the joint got there: `out_of_range` has a margin
(`OUT_OF_RANGE_DEG`), and a joint the servo parked at its limit and that then sagged past it
qualifies too.

**What the bench showed is written down where refs live.** A new VERIFIED ref,
`POSITION_LIMITS_CLAMP_GOALS`, cites `feetech.py` 268 to 276 and the bench. The open question in
`DEGREES_NO_CLAMP`, what the firmware does with an unclamped degrees goal, is answered there.
`JOINT_RANGES` now says a calibration that never saw a joint folded leaves the fold past the
travel. The test double, `FakeArm`, clamps a body joint's goal to whatever travel the test gives
it while letting a reading sit past it, which is the one property the old one lacked and the
reason the old behaviour passed.

## Consequences

- **The fix at the bench is a calibration that saw the fold.** When `lerobot-calibrate` asks for
  every joint to be moved through its whole range, take each one into the fold the arm will
  rest in. The fold is then inside the travel, the servo can be driven to it, and there is no
  note. A calibration that records a wider travel moves that joint's zero, because a joint's
  zero in degrees is the middle of the travel its calibration recorded, so a pose recorded
  under the old calibration names a different shape under the new one. The rest pose has to be
  recorded again, and a `.duck` or a remembered note that names an angle names a different pose
  afterwards. The note tells a person to calibrate folded and record the pose again. The first
  run guide and the checklist also say why the old pose goes stale, and the guide says the same
  of every task and remembered note that names an angle.
- **Parking at the edge is not the fold.** An arm released at its limit is short of the pose its
  owner recorded by the distance the note names. A joint folded past its floor may fall the rest
  of the way under its own weight. One folded past its ceiling may not, and stays where it was
  released. Either way it is released, which it was not before, and a run that parked there
  starts from the edge rather than from the fold.
- **Still unverified, and only the arm can settle it.** Nothing in this change has run on an
  arm yet. Whether a joint let go at the edge of its travel settles onto its fold, and whether
  it does so gently, is the first thing a bench should watch, starting with `quackd doctor` on
  an arm whose pose lies past its travel: the note, a green `rest pose` row, and an arm released
  at the edge. `take_hold()` no longer rests on `TORQUE_ENABLE_HOLDS_PRESENT` for a joint
  placed past its travel, since it will not switch torque on under one. The row is still
  UNVERIFIED for every other re-enable, a connect's own included.
- **Some readers do not say it yet.** `quackd robot show` prints the pose as recorded and not
  the reachable one, because it does not connect and so has no calibration to clip against. A
  flock member's rest move does not narrate the note.
- [ADR-0036](0036-what-the-arm-does-not-say.md) and [ADR-0039](0039-an-arm-placed-by-hand.md)
  are amended rather than reversed, and each carries the amendment. The range refusal, the step
  cap and the rule that torque comes off only at the rest pose all stand. What changed is what
  "the rest pose" means on an arm whose calibration cannot reach it, which joints a hold
  writes, and that a take-hold refuses a joint placed past its travel.
