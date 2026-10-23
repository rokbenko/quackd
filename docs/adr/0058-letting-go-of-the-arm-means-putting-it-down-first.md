# ADR-0058: Letting go of the arm means putting it down first

**Status:** accepted · **Date:** 2026-10-23 · Amends [ADR-0039](0039-an-arm-placed-by-hand.md) (its second door, which released the arm wherever it stood) and [ADR-0045](0045-a-rest-pose-the-calibration-cannot-reach.md) (the ways out its torque line names) · Implemented in `quackd/release.py`, `quackd/cli.py` (`robot release`, and `run`'s refusal), `quackd/agent/loop.py` (`_offer_release`), `quackd/registry.py` (`no_rest_pose_refusal`), `quackd/adapters/factory.py` (`needs_rest_pose`), `quackd/mcp_server.py` and `adapters/lerobot/` ([page](../adapters/lerobot/README.md#releasing-it), [first run](../adapters/lerobot/first-run.md), [checklist](../adapters/lerobot/hardware-checklist.md))

## Context

An SO-101 has no brake. Torque is the only thing holding it up, so an arm let go of where it
stands falls onto whatever is under it, and that is why quackd lets go of the arm, on its own
initiative, only at its recorded rest pose ([ADR-0039](0039-an-arm-placed-by-hand.md),
[ADR-0045](0045-a-rest-pose-the-calibration-cannot-reach.md)).

On the bench of 2026-09-23 every run that got to its end kept torque on at a pose it could not
reach, and finished at the power switch. 0.14.0 answered that with a second door: `quackd robot
release NAME`, and an offer a run made at its terminal when its last fold missed, both of which
told the person to hold the arm and then took torque off wherever it stood. That was a way out
short of the power switch, and a way out that dropped the arm into whoever was holding it, or
onto the table if nobody was.

On 2026-10-23 Rok asked two things of the arm. Whether the kill switch that takes torque off lets
it drop, because when anything goes wrong Rok wants a command that releases torque without
pulling the power, and releasing should mean going to the rest pose, not dropping to the table.
And whether a run that fails, an error or a model refusing the work, puts the arm back at its
rest pose.

The second was already so. Every way a run ends, a refusal, an `infeasible` verdict, a failure,
a spent budget, an error, a Ctrl-C, goes through one teardown that stops the arm, folds it to its
rest pose, and lets go there (`test_every_way_a_run_ends_returns_the_arm_to_rest`). The kill
switch itself is the first Ctrl-C or `q`, and it never let go of anything: it stops the arm and
the teardown folds it. What dropped the arm were the two paths a person reaches for after
something has gone wrong: the command, and the offer. Both released it where it stood.

A third path dropped it with nobody reaching for anything. A run, a dry run or an MCP session of
an arm with no rest pose recorded ends in a close that lets go of the arm wherever it stands,
which is how the arm of 2026-09-15 fell at the end of every run, and a bare
`--robot lerobot:real` has no name to keep a pose under at all.

## Decision

1. **`quackd robot release` folds the arm first.** It connects, folds the arm to its recorded
   rest pose as a run's teardown does, and lets go there through the first door,
   `let_go(asked=True)`, which refuses anywhere but that pose and so can never drop the arm. Its
   warning, printed before it connects, tells the person to support the arm while it connects,
   since connecting takes torque off every motor for a moment, and then to take their hands
   away when it says so: once connected it says it folds in 3 seconds (`HANDS_AWAY_S`), and a
   Ctrl-C or `q` in those seconds stops it with nothing moved. The fold used to start a few
   milliseconds after the connect, under the hands still supporting the arm.

2. **A fold that misses is settled by two questions, in this order, in both places.** The end
   of a run asks them itself instead of its old offer, and the command asks the same ones, from
   one function (`quackd.release.settle_missed_fold`), so the two cannot come to say different
   things to a person at the same arm. In Rok's words: try to clear the way and try again first,
   and only if that is unsuccessful after one try, hold it and press Enter to drop it.
   - The first asks the person to clear whatever is in the arm's way and press Enter, and the
     arm folds once more. A fold that arrives is let go of at the pose.
   - Only when that fold misses too does the second come, and it says before anything else that
     Enter now takes torque off where the arm stands and that the arm will drop, so to hold it
     first or it can be damaged. Enter releases it through the second door.
   - Anything but Enter at either question, a wait that runs out (60 s each), a Ctrl-C, a `q`, a
     terminal with nothing to read, keeps torque on and leaves the arm holding itself up where its
     fold stopped. Nothing ever drops an arm but that one Enter.
   - Neither question is put where nobody can act on it, as the old offer was not: on a dry run,
     over MCP, in a flock, on the simulator, over an arm still in somebody's hands, or over an arm
     that stopped answering, whose second fold may not even have reached it.

3. **A fold a person asked for stops when they say so.** It runs raced against a fresh Ctrl-C or
   `q`, which cancels it at its next await, so it goes on at most the one step cap whose goal was
   already sent; a stop then holds it, and nothing is released. The fold never outlives the call
   that raced it, even when that call is itself cancelled, because a fold left running would go
   on sending goals while the close decides whether to let go.

4. **`--yes` skips only the question asked before the connect.** The two questions are put
   wherever a terminal can prompt, `--yes` or not, because each is a person deciding with their
   own hands. With no terminal, a fold that arrives lets go at the pose, as every run's close
   does, and one that misses tries nothing more, keeps torque on and exits 1.

5. **An arm with no rest pose is asked about before anything connects.** It has nothing to fold
   to, and its close lets go of it wherever it stands, so connecting to it is already the drop.
   Its one question says so; Enter connects and releases it into the person's hands, anything
   else connects nothing. With no terminal the command refuses, `--yes` or not.

6. **No flag skips the fold.** A `--where-it-stands` would be the 0.14.0 command behind a flag a
   shell's history hands back to the next person, and every case it would serve has a way out
   already: the second question, a Ctrl-C that keeps torque on, `quackd robot rest-pose NAME
   --clear` for a recorded pose that is itself wrong (the command then asks before it connects),
   and the power switch.

7. **Only a bare Enter is a yes, and only one read after the question was put.** Every question
   here moves the arm or drops it, so a line with anything typed on it is a no, `n` the
   commonest, and an Enter read within half a second of the question being put
   (`TYPED_AHEAD_S`) was typed before anybody could have read it and is passed by. A press of the
   kill switch is never an Enter, at any wait: a terminal in its usual mode hands `q` over only
   with the Enter that sends its line, and the stop key used to read as a yes.

8. **The close says what was done.** Its line for an arm left holding itself up names the one
   command that puts it down, what it does, and the support it needs while it connects, and no
   longer `doctor --robot NAME` "to park it", since the release now parks it and asks before it
   drops anything. After a release asked for at the pose, it says the arm rests limp there because
   the person asked (`LIMP_AT_REST_AS_ASKED`), not that it was let go of for them to place, which
   is `--by-hand`'s line.

9. **A real arm with no rest pose is refused a run, a dry run and an MCP session.** Rok's call,
   out of the three that were asked: refuse, keep torque on at the end of such a run, or leave
   it as it had been since 0.9. A dry run is refused too, because it connects and closes like
   any other. `quackd run`, `quackd serve-mcp` and a flock refuse a `lerobot:real` arm with no
   recorded rest pose before anything is built or connected, a run once its task file has been
   judged against the body, so that a task that could never run there says so first. The refusal
   names the robot and `quackd robot rest-pose NAME`. A spec on its own is refused the same way,
   with the registration to do first, and a spec in a fleet, from `--robots` or a task file's
   `robots:`, with a stored flock to run it from after that, since neither spelling reads the
   registry. A pilot flock's member named by `--robot NAME` is built as that registered robot,
   with its address, cameras and pose, where it used to be built as a bare spec of its kind with
   none of them. The adapter declares which of its backends this applies to
   (`REST_POSE_BACKENDS`, read by `quackd.adapters.factory.needs_rest_pose`), so no LeRobot name
   enters the core, and `run_pilot_flock` refuses one as well, for a library caller. Not
   refused: `doctor` and `robot list --probe`, which on an arm with no pose move nothing and are
   run with a person supporting the arm; `rest-pose`, which is how a pose comes to exist;
   `robot release`, which asks before it connects (5); the mock; and the simulator, which drops
   nothing real.

What does not change: the first door's rule, the first Ctrl-C or `q` of a run, which still stops,
folds and lets go at the pose, a second Ctrl-C during the teardown's own first fold, which still
quits at once and leaves the arm holding, `doctor`, which still parks and lets go at the pose,
and the last decision of ADR-0039: no verb, no MCP tool, and not on the `RobotAdapter` protocol.

## Consequences

- `quackd robot release` now moves the arm, at the step cap (5 degrees a send at 10 Hz by
  default). A rest pose recorded before a recalibration can fold the arm toward a shape that is
  no longer its fold. The warning before the connect, the fold's own stall stop and a Ctrl-C are
  what stand between that and the arm, and the bench is owed a run of it.
- The record of a run whose fold missed gains `release` stages `refold` and `folded` and new
  `kept` reasons (`interrupted during the fold`, `the arm did not answer the second fold`), and a
  `prompt` row per question.
- `AgentLoop.RELEASE_OFFER` and `RELEASE_OFFER_S` are gone. The waits are
  `quackd.release.CLEAR_THE_WAY_S` and `ENTER_DROPS_S`.
- Every connect still takes torque off for a moment, because LeRobot configures the motors inside
  `torque_disabled()`. A command run after a crash has to connect, so that moment stays, and so
  does the warning about it.
- A real arm is no longer run by its spec. `--robot lerobot:real --address PORT` reaches it for
  `doctor`, and a run, a session or a flock wants the name it was registered under, with a pose
  recorded there. `quackd robot rest-pose NAME --clear` on a real arm says so.
- The declaration is read from the installed adapter, so a core from this release beside an
  adapter from an older one, a mixed install pip warns about and goes ahead with, has no backend
  to refuse and lets such a run through as before.
- Nothing here has run on an arm: the bench steps in the CHANGELOG are owed.

## Alternatives

- **Keep the old command and add a folding one beside it.** Rok asked for releasing to mean
  folding, not for a second command, and the old one is the one the close's line sent people to.
- **One question after one missed fold, as the old offer was.** A fold that stalled against a hand
  or an object usually arrives once the way is clear, which is what Rok asked to try first.
- **More than one retry, or a retry without asking.** One retry, after the person has cleared the
  way, and then the person decides; a fold that keeps hitting something is not made again unasked.
- **Let a wait that runs out drop the arm.** Never: an empty room is the case a timeout is for, and
  nobody is holding an arm in an empty room.
