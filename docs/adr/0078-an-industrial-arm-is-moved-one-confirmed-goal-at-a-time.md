# ADR-0078: An industrial arm is moved one confirmed goal at a time, and never brought up by quackd

**Status:** proposed, for Rok's word before the change that brings it merges · **Date:** 2026-11-08 · Follows [ADR-0075](0075-an-adapter-is-a-family-a-body-file-and-a-binding.md) (a family over another package's binding) and [ADR-0076](0076-every-backend-has-a-tier-and-bound-code-names-its-owner.md) (bound code with no owner is refused in its words) · Implemented in `adapters/rosarm` (`quackd-rosarm`) · Documented in [docs/adapters/rosarm/README.md](../adapters/rosarm/README.md)

## Context

quackd 0.33.0 describes four industrial arms that nothing drives: a Universal Robots UR5e, a KUKA
LBR iisy 3 R760, a KUKA LBR iiwa 7 R800 and a Yaskawa MotoMini. Each has a ROS 2 driver, its maker's
or, for the iiwa, the LBR-Stack, that a client reaches through `rosbridge_server`, and quackd
already speaks rosbridge through roslibpy for a wheeled base. ADR-0076 lets that code in with no
owner only as described, refused at `make()` until somebody names a date for a first hardware run.
So the family that drives these arms is rehearsed against a fake bridge and a fake arm, and the
calls below are the ones its code makes on every arm, which no earlier record covers.

What decides them is what the sources say, read at pinned commits on 2026-11-08
(`adapters/rosarm/src/quackd_rosarm/upstream_api.py`). Each arm's driver guards the link between
itself and its controller, and none guards the link to a client like quackd: rosbridge cancels
nothing for a client that has gone, the JointTrajectoryController's own timeout is off by default,
and MotoROS2 is read to watch only its own agent. A bridge that runs each goal on the thread that
reads its client, as Humble's stock launch does, reaches a cancel only after the move has finished.
A SUCCEEDED proves little where the tolerances are left at their default of 0. And three of the arms
are brought up by services quackd could call: UR's dashboard server loads and plays its program,
KUKA's `robot_manager` activates its controllers, MotoROS2's `start_traj_mode` turns its servos on.

## Decision

**quackd never powers, enables, un-brakes or un-stops an arm.** It sends two operations and nothing
else: a FollowJointTrajectory goal and its cancel. No service that powers an arm, turns its servos
on, releases its brakes, clears an alarm, unlocks a protective stop, plays a program or moves a
driver through its lifecycle. The person at the cell brings the arm up on its pendant, and quackd
sends no goal while the driver says the arm cannot move. A test holds the transport to what it
sends: the goal, the cancel, three questions to rosapi, and its subscriptions and their ends. One
thing it cannot rule out is said where it holds: a goal that reaches a MotoMini whose energy saving
function has turned the servos off makes MotoROS2 turn them on itself.

**No rest pose.** quackd parks a body only to put it down before it lets go, the SO-101's falls. An
industrial arm's controller and its brakes hold it where it stops, and a move nobody asked for at
the end of a run is the hazard on a twenty kilogram arm, not the cure. `go_to_rest()` answers none
with that sentence, and a recorded rest pose is refused.

**Every move asks a person first**, on the mock as well as on `ws`, since a fake is never kinder:
`move_joints` is `safety_class="confirm"`. On `ws` a person answers each move: a standing answer
does not count for it (`Verb.no_standing_answer`, a backend's declaration on its own verb), so
`--yes`, a flock's standing answer and an MCP session are refused before its question is put, in
words that say to run it from a terminal, without `--yes`, and answer it. This was left open here
until Rok decided it on 2026-11-09. On the mock `--yes` goes on answering it, as it answers a
`confirm` verb on any body, so a rehearsal can script. A script answering `quackd run`'s question
on stdin still answers it on both backends. A move is one goal to every joint, at most at a tenth
of each joint's rated speed at the fastest point of the segment the controller plays, never sent
over a running one, and arrived only when every joint reads within half a degree of its goal on
`/joint_states`, whatever the action said.

**A bridge that cannot cancel in time is refused at connect.** quackd asks the bridge for its own
`send_action_goals_in_new_thread` through rosapi and refuses one that says false, or whose answer it
cannot read, in words that name rosbridge_suite issue #909 and the flag that fixes it. A stop quackd
cannot vouch for is no stop.

**When quackd goes quiet, one move runs on.** Nothing between quackd and the arm ends a goal on its
client's silence, so the arm finishes the move it was given and holds there. The bound is that move:
at most 12 s, at the speed asked. The manifest says so rather than implying a deadman,
`safety_authority` `estop` with no deadman, and a datasheet note says the pendant's stop is the
brake. The heartbeat cancels what it can reach, and ends the run, when the socket drops, when the
readings stop, and when the driver stops the arm itself while quackd watches. A client whose socket
dropped is let go of for good, since roslibpy sends what it held back once it has reconnected by
itself, and a goal held back that long is one nobody is watching.

## Consequences

- A tighter bound is possible later, and not taken now: a chain of goals one second long, each sent
  before the last ends, would stop a JointTrajectoryController arm within a second of quackd's
  silence. MotoROS2 is read to take no goal over a running one, so it would not stop that arm, and
  the churn of goals ended early is its own risk.
- A Humble bridge needs `send_action_goals_in_new_thread:=true` on its launch line before quackd
  connects to it at all. Jazzy's launch file already says true.
- A task that wants a home pose asks for one with `move_joints`, and a person confirms it.
- The fake the mock runs on is written from reading. A job in CI against a real controller, on
  ros2_control's mock hardware in a container, is what would show where the reading is wrong, and it
  is owed, not part of this record.
- These calls are Rok's to change before the change that brings them merges: the confirmation on
  every move, the speed ceiling of a tenth, the half degree of arrival, and refusing `rosarm:ws` at
  `quackd robot add` as well as at `make()`.
