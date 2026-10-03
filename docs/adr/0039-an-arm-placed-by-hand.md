# ADR-0039: The one place quackd lets go of a robot

**Status:** accepted, amended · **Date:** 2026-09-18 · Extends [ADR-0036](0036-what-the-arm-does-not-say.md) (which said quackd never disables torque) and [ADR-0012](0012-safety-executor.md) (the kill switch, and what a second Ctrl-C means) · Implemented in `quackd/agent/loop.py`, `quackd/safety.py` and `adapters/lerobot/` ([page](../adapters/lerobot/README.md), [checklist](../adapters/lerobot/hardware-checklist.md))

**Amended 2026-09-23 by [ADR-0045](0045-a-rest-pose-the-calibration-cannot-reach.md):** "the
recorded rest pose" in the first decision below now means that pose as far as this arm's
calibration lets the servo be driven toward it. A joint recorded past its calibrated travel is
at rest within `TOL_DEG` of the edge of that travel or anywhere beyond it on the side of its
fold, and `let_go()` still asks exactly what `close()` asks, so `--by-hand` releases an arm
parked at the edge and one already folded past it. The second decision gains a refusal ahead of
it: `take_hold()` does not switch torque on while any body joint the person placed reads
outside its travel. A goal written where that joint reads lies past the travel, is clamped to
the limit and drives the joint there under their hand. No goal leaves the servo the last one it
was written, which after a hand-off is the rest move's, possibly the far end of the travel, and
whether torque coming on holds the joint where it is instead is `TORQUE_ENABLE_HOLDS_PRESENT`,
still unverified. Neither keeps the joint where it was put, so nothing is written, torque stays
off, the arm stays in their hands, and the refusal names each joint, its reading and its
travel. The run ends there. (This amendment first said the joint was left out of both writes
and left to its servo, with the read-back to catch it, and that could be the larger of the two
motions. ADR-0045 has the whole of it.)

**Amended 2026-09-24:** the rule for an arm a take-hold refused, whatever refused it. While an
arm is, or may be, in a person's hands, quackd writes it no goal, does not move it and does not
switch its torque on again on its own, and it tells the person torque is off only where nothing
was sent or a read said so. A take-hold refused before its torque write went out (a joint placed
past its travel, a closed transport, nothing to hold), or one whose read after that write found
every motor off, left the arm as the release did, limp, and the person is told quackd did not
take hold and the arm is still in their hands, or, where the take-hold's own read found the
whole arm at its rest pose with every motor off, that it is still limp at its rest pose and
which joints to lift inside their travel for quackd to take hold. One refused after it (a torque
register that did not answer, a torque call that raised) may have left the arm energised, and
the person is told quackd could not confirm whether it has torque, to hold it as though it may
move or drop, and to cut its power to be sure, and one whose read found motors on beside others
off is told which joints hold and that the rest is limp, to keep hold of the arm and to cut its
power (`HandResult.energised` says which, `torque_on` which motors). That speaks for every
take-hold since the release: one refused before its own write after an earlier one an interrupt
cut off past its write says what the reads since that write say. A read speaks for the arm only
when the bus carried it after the last torque write, since the run's heartbeat reads on its own
clock. After any refusal, the teardown's stop takes no second hold and sends nothing, the
hand-back is not asked, the rest move writes nothing and the run says the arm is not folded (or,
where a read finds it at its rest pose, that it is already there), and the close says what its
own read found: an unconfirmed torque write as unconfirmed, joints read on by name, an arm the
placing release let go of that still reads at its rest pose with every motor off as limp at that
pose, and otherwise the arm limp in their hands, let go of for them to place. The stop still
picks an arm up out of a hand, as the decision below on the stop says, where no take-hold has
been refused since the release, which is a Ctrl-C during the placement wait: it energises the
arm where the hand has it and the rest move folds it. Where that take-hold is refused, the
person is told so once and it is recorded, as the one at Enter is. An earlier version of the
amendment above said the teardown after a refusal "closes on the note for an arm in somebody's
hands, never on the one about torque left on", and a person who moved the joint back inside its
travel had torque switched on under their hand by the stop or by the stall of the rest move,
with nothing said, and the run closed on torque left on. And a torque register that did not
answer at Enter was told as quackd never having taken hold, and the rest move then folded the
energised arm under the hands it had just told to keep hold of it. (The first version of this
amendment told an Enter over a fold nobody lifted as an arm in the person's hands to keep hold
of, and a read that found motors on in part as one that could not be confirmed. It left the
stop's own refusal unsaid, in `stop_error`, and judged a read by where the coroutines were, so a
heartbeat read that got the bus ahead of the torque write let the close call an energised arm
limp.)

**Amended again 2026-09-23, by `quackd robot release`:** the rest pose is no longer the only
place quackd releases an arm. A person at the arm can now ask for its torque by name, wherever
it stands, because on the bench that day every run that got to its end kept torque on at a pose
it could not reach and finished at the power switch. `let_go(anywhere=True)` skips the two
refusals about the pose and nothing else: the joints are read first, the release is read back
off every motor, and the arm is in somebody's hands afterwards, so the close says
`LIMP_IN_HAND`, or, where that read-back found motors still on, names them and says to cut the
power (`still_holding_in_hand`), or, where nothing read the release back, says that and to cut
the power to be sure (`UNREAD_IN_HAND`). Two things open it, and both are a person at a
terminal. `quackd robot release NAME` warns that connecting takes torque off for a moment and
that the release lets the arm fall, asks, and only then connects. A run whose last rest move
missed, over an arm that still answered, offers the same before its close, Enter within
`AgentLoop.RELEASE_OFFER_S` (60 s), and only where the CLI could prompt (`RunConfig.person`,
which is not `hand_off`): never on a dry run, over MCP or in a flock. The first decision below
stands for `--by-hand`, which still releases at the rest pose and nowhere else, because there
the release comes before anybody's hands are on the arm, and here the hands come first. The last
decision stands whole: no verb, no MCP tool, and not on the `RobotAdapter` protocol. One reading
moved with it, in both doors. A release is `released` from the moment it is sent, so a release
call that raised part way through its motors, or that a Ctrl-C or a cancellation landed on, now
leaves the arm in a hand instead of reporting it released in nobody's, and a read that failed
*before* the release now refuses instead of reporting a release that never went out.
`HandResult.torque_on` names the motors that still read on, so nobody is told torque reads off
over a joint that kept it.

## Context

Every run of an SO-101 has started the same way since the arm had an adapter: quackd drives it
to the pose recorded with `quackd robot rest-pose`, and the pilot takes it from there. That is
the right default and it stays the default. It is also the wrong start for most of what an arm
on a bench is actually for. Drawing needs a pencil already in the gripper. Stacking needs a
block already held. Anything over a sheet of paper needs the arm leaning over the paper, not
folded beside it.

There were two ways to get there and both are bad. A person can record a different rest pose,
which means teaching the arm a number for a position they could reach out and set in two
seconds, and which then becomes the pose every later run and every teardown folds to. Or the
pilot can jog there through `move_joints`, which spends model calls and budget getting to the
place where the task begins, one [ADR-0036](0036-what-the-arm-does-not-say.md) step cap per
tick, and ends somewhere approximate anyway.

The obstacle is that an arm a person can move is an arm with no torque in it, and on a
position-controlled arm torque is the only thing holding it up.
[ADR-0036](0036-what-the-arm-does-not-say.md) closed its decision with the flat sentence that
quackd never disables torque, and meant it: every gap that ADR closed was a gap between an LLM
and a servo. This is not that. A person standing at the arm with both hands on it is a
different situation from a model three seconds away, and it is the only situation in which
letting go is a service rather than a drop.

## Decision

- **The recorded rest pose is the only place an arm is released, on exactly the condition
  `close()` already trusts.** `let_go()` refuses anywhere else, naming what is out of place
  (`the arm is not at its rest pose (...), and an arm held up by torque alone falls when
  torque goes`). The test is `at_rest`, which is the same read `close()` makes before it lets
  `disable_torque_on_disconnect` stay true: quackd has been dropping torque at that pose at
  the end of every ordinary run since [ADR-0036](0036-what-the-arm-does-not-say.md), so it is
  the one pose this repository already treats as safe to leave an arm in with nothing holding
  it. So `--by-hand` drives to the rest pose first and releases there, and the person lifts
  the arm out of a pose it was standing in on its own. An arm with no pose recorded is refused
  before anything connects.
- **The present position is written as the goal before torque comes back on, and again after.**
  `enable_torque()` writes one register and nothing else, so what a servo does with the goal it
  was last told is the firmware's business and is documented nowhere at the pin: that is the
  new UNVERIFIED ref `TORQUE_ENABLE_HOLDS_PRESENT`. It matters because the goal last written
  before a hand-off is the rest pose the arm has since been lifted out of by hand, so a servo
  that drives to it drives back to the fold with somebody's fingers in the way. Writing where
  the arm is now, first, makes both readings of the undocumented behaviour end in the same
  place. Writing it again, sleeping a tick and reading back is how `take_hold()` answers with
  what happened rather than with what should have: the assumption is relied on in neither
  direction.
- **A slip refuses the run and still reports the arm as being in nobody's hands.** Those are
  two different facts and the code keeps them apart. `_in_hand` is cleared the moment torque
  reads on, before the pose is compared at all; only then does `take_hold()` refuse a joint
  that moved more than `TOL_DEG` (5.0 degrees), saying which joint and by how much. An arm
  that sagged as torque came on is holding itself perfectly well, just not where it was put,
  and the run stops because the pilot was promised a starting pose nobody now has. Reporting
  that arm as limp in somebody's hands would send a person to cut the power on a robot that
  needs nothing.
- **Enter is read through the kill switch, never with an `input()` of its own.** The switch
  already runs the only thread quackd points at stdin. A second reader would race it for the
  same keystroke and whichever lost would sit forever on a line the other had taken. So
  `KillSwitch` grew `entered`, `pressed`, a `presses` count and `wait_for_enter()`, and
  `_watch_keys` now loops until stdin ends instead of stopping at the first `q`: a run that
  hands the arm over waits for a person after the abort flag may already be set, and waits
  again inside its own teardown, so the reader has to outlive both. The CLI's
  `_TerminalHandOff` prints a line and waits on the switch, and is bound to it after the loop
  exists, because the switch is built from the loop's own abort event.
- **The end-of-run wait watches a fresh key press rather than the abort flag.** The first wait,
  while somebody places the arm, watches `abort`, which is how a Ctrl-C rescues a run whose
  operator walked away. The second, between the run's `stop` and the fold, cannot: on every run
  a person ended, the abort flag is already set before that wait begins, and watching it would
  skip the question on exactly the runs most likely to still have something in the gripper. It
  waits on `pressed` instead, which every waiter clears on the way in, so a fresh Ctrl-C ends
  it and a stale one does not. It is bounded at `AgentLoop.HAND_BACK_S` (120 s), because a run
  must still finish when the room is empty: the cost of waiting is an energised arm and the
  cost of not waiting is a pencil driven into the bench as the arm folds.
- **The second Ctrl-C is caught in that one window and nowhere else.**
  [ADR-0012](0012-safety-executor.md) hands SIGINT back after the first press on purpose, so a
  human who is not convinced the first one worked is never holding a key quackd has swallowed,
  and a second press raises straight through the teardown. `_hand_back()` catches
  `CancelledError` and `KeyboardInterrupt` on its own wait, because there the press means
  "skip the question", not "abandon the arm energised with no record written". The gripper is
  left exactly as the run left it, the arm still folds to its rest pose, the transport still
  closes, and the record still ends with a `run_end` and a summary. It buys one press: a third
  lands somewhere without a guard and quits at once.
- **A stop picks the arm back up before it holds it.** Every teardown begins with a stop, so a
  stop is what a Ctrl-C during the hand-off wait actually reaches, and sending a goal to a limp
  servo is a stop that stopped nothing. `_hold()` therefore calls `take_hold()` first whenever
  the arm is in a hand, which re-energises it where the person has it so that the rest move
  after can put it down. Where a run ends limp anyway, `close()` prints the new `LIMP_IN_HAND`
  note instead of the torque note, because telling somebody holding a limp arm that it is
  holding itself up is the single wrong answer that gets an arm dropped.
- **No verb asks for any of this, and no model can.** `let_go` and `take_hold` are not verbs,
  are not reachable over MCP, and are deliberately not on the `RobotAdapter` protocol, which is
  runtime-checkable and structural: a method there is a method all seven bodies must carry, and
  six of them are never handed to a person. A body that can be declares
  `supports_hand_off = True`, and the loop reaches it through `let_go_if_any` and
  `take_hold_if_any`, the same way a rest move reaches a bare transport. Each is called once,
  in a fixed order, only on a run a person started with `--by-hand` at a terminal. The pilot is
  told where its run begins (`build_system_prompt(..., by_hand=True)`) and nothing more: it
  cannot ask for torque to come off any more than it could before this change.

## Consequences

- Five refusals fire before anything connects and before a run directory exists: a body that is
  not the arm, an arm with no rest pose recorded, several robots, `--dry-run` (which moves
  nothing at either end and is the opposite ask), and no terminal to ask on. Each names the
  command that fixes it. A run without the flag is unchanged, in its prompt, its trace and its
  teardown.
- The trace gains `hand_off`, with `stage` one of `released`, `held`, `skipped` or `unloaded`,
  so a transcript says which of the two waits a run got through. `_hand_over()` restarts the
  budget after the person is done, because an arm can stand waiting while somebody goes to find
  a pencil and that used to be spent out of the `max_minutes` the model gets for the task.
- [ADR-0036](0036-what-the-arm-does-not-say.md) is amended rather than reversed, and carries
  the amendment. What that ADR said quackd never does is now one clause shorter and no more:
  the adapter still never calibrates, still refuses an arm with no calibration file, and still
  keeps LeRobot's default of dropping torque on `disconnect()`.
- **What is now UNVERIFIED, and only an arm can settle it.** Every release quackd has performed
  was against `lerobot:mock`, which cannot sag, cannot slip and cannot be lifted. Four things
  need a bench: what a Feetech servo does with its goal on re-enable
  (`TORQUE_ENABLE_HOLDS_PRESENT`); whether an SO-101 at its recorded fold really does stand
  with torque off, which is the assumption the whole of the first decision above rests on; how
  far a hand-placed arm settles between `enable_torque()` and the read a tick later, which is
  what 5.0 degrees was guessed against; and whether a released arm is light enough to place
  one-handed while the other hand loads the gripper. The route is the one
  [ADR-0036](0036-what-the-arm-does-not-say.md) left its own numbers on: the hardware
  checklist's *What to report*, answered by somebody with the arm rather than by anybody with a
  command.
- **The two ends read the same silence in opposite directions, on purpose.** A torque register
  that does not answer straight after the release is treated as a release that took: the other
  reading ends with `close()` telling a person holding a limp arm that it is holding itself up.
  A torque register that does not answer after `enable_torque()` is treated as no hold at all
  and refuses, because the other reading ends with quackd telling that same person they can let
  go. In both the rule is the same one: pick the answer that does not get the arm dropped, and
  never the one that sounds more decisive. Each costs something. A corrupt packet at the
  release can report an arm released that is still energised; a corrupt packet at the hold ends
  a run that might have been fine. Both are deliberate.
- **One bad register cannot speak for the other.** The status registers are read in a `try`
  each. They shared one until an adversarial pass found what that cost: `take_hold()`'s torque
  check was written to stand down whenever a register read had failed, so a corrupt
  *temperature* packet switched off the check on *torque*, and an arm that ignored
  `enable_torque()` was reported as holding the pose a person had just set. The line they were
  about to read said they could let go.
