# rosarm (an industrial arm on ROS 2, rehearsed only)

**Rehearsed only.** `rosarm:mock` runs whole tasks against a fake rosbridge and a fake arm, and
`rosarm:ws`, the backend that would reach a real arm, refuses every arm until somebody names a
date for that arm's first hardware run. Nothing here has met a controller or an arm, and the
transport has met a real rosbridge only in CI, against a stand-in for the arm's driver
([Status](#status)).

> [!WARNING]
> quackd is not a safety system. On an industrial arm the risk assessment, the guarding, the
> emergency stop and the pendant are the integrator's. quackd's confirmation before every move
> and its speed ceiling are courtesies, not safeguards, and when quackd goes quiet in the middle
> of a move the arm finishes that move. The pendant's stop is the brake. `--yes` answers a move
> on `rosarm:mock`, as it answers a `confirm` verb on any other body, and on `rosarm:ws` a person
> answers each move: `--yes`, a flock's standing answer and an MCP session are refused before
> its question is put. A script answering `quackd run` on stdin still answers it on both.

Four bodies, which quackd also describes from their makers' figures: a Universal Robots UR5e, a
KUKA LBR iisy 3 R760, a KUKA LBR iiwa 7 R800 and a Yaskawa MotoMini. Each sits behind a ROS 2
driver, its maker's for three of them and the LBR-Stack for the iiwa, and quackd reaches the
driver through `rosbridge_server` with
[roslibpy](https://github.com/gramaziokohler/roslibpy), as the rosbridge adapter reaches a
wheeled base ([docs/adapters/rosbridge/README.md](../rosbridge/README.md)). A registered robot
names its arm with `--robot-type`, the body file's name; a UR5e is what nobody naming one gets.
The family's own calls, why it never powers an arm, parks none, asks before every move and
refuses a bridge that cannot cancel in time, are
[ADR-0078](../../adr/0078-an-industrial-arm-is-moved-one-confirmed-goal-at-a-time.md).

```bash
uv pip install "quackd[rosarm]"
quackd list-verbs --robot rosarm:mock
quackd run rosarm-lookout --robot rosarm:mock --llm fake
quackd robot add cell-1 rosarm:mock --robot-type yaskawa-motomini
quackd run rosarm-lookout --robot cell-1 --llm fake
```

The lookout reads the arm back and moves nothing. On the UR5e, the arm nobody named, it ends:

```text
│ shoulder_pan_joint 0, shoulder_lift_joint 0, elbow_joint 0, wrist_1_joint 0, wrist_2_joint 0, wrist_3_joint 0; no   │
│ move running; the driver says program running, safety mode NORMAL                                                   │
```

and on the MotoMini registered as `cell-1`:

```text
│ joint_1 0, joint_2 2, joint_3 20, joint_4 0, joint_5 90, joint_6 0; no move running; the driver │
│ says motion possible                                                                            │
```

`quackd robot add` refuses the real backend at the door, in the words `make()` refuses it with,
so no `robots.json` holds a robot that cannot run:

```text
$ quackd robot add cell-2 rosarm:ws --robot-type ur5e --address ws://127.0.0.1:9090
✗ error: rosarm:ws on a ur5e is described and not driven: bound code needs a named owner and a date
for its first hardware run (docs/adr/0076), and nobody has named either for this arm; rosarm:mock
rehearses it (docs/adapters/rosarm/README.md#status)
```

The extra is three packages: `quackd-rosarm`, `quackd-rosbridge`, whose binding and refs this
family uses, and roslibpy, through the rosbridge package's own extra. The mock imports no
roslibpy.

## Backends

| `--robot` | Tier | What it is |
|---|---|---|
| `rosarm:mock` | ✅ rehearsed | `RosArmWs`, the transport `ws` runs, over a fake bridge and a fake arm (`fake.py`) on a clock of its own: every line from the verbs down to the calls roslibpy would make runs in the suite, in fake time, on all four bodies |
| `rosarm:ws` | 📄 described | the same transport over roslibpy 2.x to `rosbridge_server`. Bound code, refused at `make()` and at `quackd robot add` for every body until an owner names a date for that arm's first hardware run ([ADR-0076](../../adr/0076-every-backend-has-a-tier-and-bound-code-names-its-owner.md)) |

The day somebody names a date for one arm, that body's tier in `bodies.py` becomes bound with the
owner and the date, `ws` gains a connect block, and `make()` builds that body and no other. A
hardware checklist for that run is owed by whoever names the date, so there is none yet.

## The four arms

The figures, mass, payload, reach, are the body files' (`quackd/adapters/bodies/`), which the
described row reads too, so `described:ur5e` and `rosarm:mock` say the same 20.6 kg, 6 joints,
5 kg and 0.85 m of a UR5e. What a driver answers to is a record in
[`adapters/rosarm/src/quackd_rosarm/bodies.py`](../../../adapters/rosarm/src/quackd_rosarm/bodies.py),
every value a stock bringup's default read in the driver's own files. The address sets the
action, the readings topic and a prefix for every joint's name, and turns the status read off;
the joint names themselves and the status topics are the record's.

| `--robot-type` | Driver | Action | Joints | Readings | What quackd reads of the driver | `can_move` means |
|---|---|---|---|---|---|---|
| `ur5e` | ur_robot_driver, Humble or Jazzy | `/scaled_joint_trajectory_controller/follow_joint_trajectory` | `shoulder_pan_joint` to `wrist_3_joint` | `/joint_states` | `/io_and_status_controller/robot_program_running` and `/io_and_status_controller/safety_mode` | the program runs and the safety mode is NORMAL or REDUCED |
| `kuka-lbr-iisy-3-r760` | kuka_rsi_driver, built from source, on iiQKA.OS2 | `/joint_trajectory_controller/follow_joint_trajectory` | `joint_1` to `joint_6` | `/joint_states`, positions alone | `/event_broadcaster/hardware_event`, sent only when it changes | the latest event heard is no CONTROL_STOPPED or ERROR; an event that says neither, or none heard yet, is believed |
| `kuka-lbr-iiwa-7-r800` | the LBR-Stack over FRI | `/lbr/joint_trajectory_controller/follow_joint_trajectory` | `lbr_A1` to `lbr_A7` | `/lbr/joint_states`, the commands sent under its stock `open_loop` | nothing: where FRI's session state is published is not settled | the driver is believed |
| `yaskawa-motomini` | MotoROS2 0.2.1 on the YRC1000micro | `/follow_joint_trajectory`, MotoROS2's own server | `joint_1` to `joint_6` | `/joint_states` | `/robot_status` | motion possible, and neither e-stopped nor in error |

What the person at the cell does before quackd's first goal, none of which quackd does:

- **UR5e**: the arm powered on and its brakes released on the pendant, and the External Control
  program playing, unless the driver runs headless.
- **LBR iisy**: `robot_manager` configured and activated, and then, within the ten seconds the
  driver waits, KUKA's RSI program started on the controller, on which the brakes release and
  external control starts.
- **LBR iiwa**: LBRServer launched on the smartPAD with the client's address, in T1 first, and the
  stack's hardware bringup running.
- **MotoMini**: the controller in AUTO and REMOTE with no e-stop and no alarm, trajectory mode
  started with `start_traj_mode`, which turns the servos on, and a `robot_state_publisher` serving
  Yaskawa's own MotoMini description, since MotoROS2 publishes none.

A status topic whose message package the bridge's machine does not have is read as no status at
all: `report_state` says nothing has been read of the driver, and `can_move` believes the driver,
as the SO-101's `not_hot` believes a backend with no thermometer.

## What the bridge must run

- **rosbridge_suite 2.0.0 or later**, which has actions. Humble releases 2.0.8 and Jazzy 2.7.1; a
  bridge from before 2.0.0 answers no goal at all.
- **`send_action_goals_in_new_thread:=true`.** Without it the bridge runs each goal to its end on
  the thread that reads every message from its client, so a cancel is reached only after the move
  has finished (rosbridge_suite issue #909). Jazzy's launch file sets it true and Humble's false.
  quackd asks the bridge for the parameter at connect and refuses a bridge that says false, or
  whose answer it cannot read:

  ```text
  rosarm: ws://localhost:9090 runs each action goal on the thread that reads every message, so a
  stop would wait until the move had ended (rosbridge_suite issue #909); start it with
  send_action_goals_in_new_thread:=true, as Jazzy's launch file does and Humble's does not
  ```

- **rosapi**, which the stock launch starts, for the parameter, the action servers and the robot's
  description. On rosbridge_server 2.7.1, which Jazzy ships, a read of a parameter whose node rosapi
  cannot see yet ends `rosapi_node` half a second later while the bridge goes on (upstream's #1308 is
  not in it), and the next connect is refused naming that parameter, so start the bridge with
  `respawn:=true`, which brings rosapi back.
- **The robot's description** on `robot_state_publisher`'s parameter or its latched topic, with a
  lower, an upper and a velocity on every joint: a goal with no travel to check it against is one
  quackd will not send.

## The address

```
ws://HOST:9090?action=/scaled_joint_trajectory_controller/follow_joint_trajectory&joint_states=/joint_states&prefix=&urdf_param=/robot_state_publisher:robot_description&urdf_topic=/robot_description&bridge_node=/rosbridge_websocket&status=on
```

Every key defaults from the body record, so `ws://cell.local:9090` is enough for a stock bringup.
`prefix` goes before each joint's name (UR's `tf_prefix`), `urdf_param` and `urdf_topic` take
`off`, `bridge_node` names a bridge started under another name or a namespace, and `status=off`
reads no status topic of the driver's. A key quackd does not read is refused, since a typo read
as nothing
would put a stock default in front of an arm somebody configured otherwise.

At connect each check is a refusal that says what it found: the thread parameter, the action among
`/rosapi/action_servers`, no goal still running on that action, the first `/joint_states` naming
every joint, read by name and never by place, and the description's limits. A goal still running
then was sent before quackd connected, by another client or by an earlier run that went quiet in
the middle of a move, and quackd reads it off the action's own status, which the controller behind
the UR5e, the iisy and the iiwa publishes whenever a goal changes and latches:

```text
rosarm: /scaled_joint_trajectory_controller/follow_joint_trajectory is still running a goal sent
before quackd connected (executing): another client's, or one an earlier run left going when it went
quiet, so the arm may be moving. quackd sends no goal over a running one: wait for it to end, or
stop it on the pendant, then connect again
```

MotoROS2 is read to publish no such status, so on the MotoMini nothing is heard and nothing is
refused, and quackd's first goal there waits behind one still running, unanswered until that one
ends. Nor is a goal another client sends after the check looked for, or a trajectory published on
a JointTrajectoryController's own topic, which has no goal and so no status: quackd's next goal
would replace either.

## The manifest

`rosarm:mock` on the UR5e, cut to the fields quackd decides:

```json
{"manifest": 1, "id": "cell-01", "vendor": "universal-robots", "model": "ur5e", "embodiment": "arm", "mobility": "none", "intents": ["joint"], "sensors": ["joint_state"], "verbs": ["report_state", "stop", "move_joints"], "safety": {"report_state": "safe", "stop": "safe", "move_joints": "confirm"}, "preconditions": {"move_joints": ["fresh_state", "can_move"]}, "safety_authority": {"native": "estop", "deadman": false, "heartbeat_hz": 2.0}, "frame": {"reference": "base", "note": "joint space in degrees; no camera, no Cartesian goals"}, "limits": {"speed_share": 0.1}, "extras": {"robot_type": "ur5e", "joints": ["shoulder_pan_joint", "shoulder_lift_joint", "elbow_joint", "wrist_1_joint", "wrist_2_joint", "wrist_3_joint"], "action": "/scaled_joint_trajectory_controller/follow_joint_trajectory", "joint_states": "/joint_states", "status_topics": ["/io_and_status_controller/robot_program_running", "/io_and_status_controller/safety_mode"], "speed_share": 0.1, "tolerance_deg": 0.5, "joint_range_deg": {"shoulder_pan_joint": [-360.0, 360.0], "shoulder_lift_joint": [-360.0, 360.0], "elbow_joint": [-180.0, 180.0], "wrist_1_joint": [-360.0, 360.0], "wrist_2_joint": [-360.0, 360.0], "wrist_3_joint": [-360.0, 360.0]}}}
```

The figures are the body file's: 20.6 kg, 6 joints, 5 kg of payload and 0.85 m of reach, each
the maker's own. `safety_authority` says `estop` and no deadman, because nothing between quackd
and the arm ends a goal when quackd goes quiet. The travel is the robot's own description,
published to a tenth of a degree rounded inward; `ws` claims none until a bridge has served it.

## Verbs

| Verb | Offered | Why |
|---|---|---|
| `report_state` | yes, the family's own | every joint in degrees to a tenth, whether a move is running, what the driver says about itself, how old the oldest of the joints' latest readings is, what the readings are on this body, and that it holds nothing |
| `stop` | yes, core | cancels the running goal and waits a bounded time for its CANCELED result; never gated |
| `move_joints` | yes, the family's own, confirmed by a person every time. On `rosarm:mock` `--yes` or a script answering `quackd run` on stdin may answer it; on `rosarm:ws` a standing answer is refused and only a person, or such a script, answers it | the SO-101's parameters exactly, `positions` in degrees by joint name and `duration_s` |
| `gripper`, `place`, `pick`, `manipulate` | no | no gripper or tool is fitted: what goes on its flange is the integrator's choice, and quackd drives none |
| a rest pose | no | quackd parks an industrial arm nowhere: its controller and its brakes hold it where it stops, and a move nobody asked for at the end of a run is the hazard, not the cure |
| a hand-off | no | hand guiding a cobot is a mode on its own pendant |
| `observe` | no camera here | a `--host` board's camera gives it, as it gives any body |

How `move_joints` works underneath:

1. **Every joint, always.** A joint the pilot did not name is sent at its present reading: the
   JointTrajectoryController refuses a goal that leaves one out by default, and MotoROS2 always.
2. **Two points, both at rest**, under a header stamp of zero: the present reading at a time from
   start of 0 and the goal at `duration_s`, each with zero velocities and accelerations. It is the
   one shape all four take, and on the controller it is a quintic that starts and stops smoothly.
3. **One goal at a time.** quackd never sends a goal over a running one: MotoROS2 is read to
   take a second goal only once the first has ended, and one rule for four arms is the simpler
   one.
4. **Arrival is quackd's judgement.** After the result, every joint must read within 0.5 degrees
   of its goal on `/joint_states`, or the verb fails naming the joint furthest off. A SUCCEEDED
   proves little where the tolerances are left at 0, as they are on the iisy and the iiwa. A joint
   within 0.5 degrees of its goal is at it, so a goal that near where every joint already reads
   counts as arrived whether or not the arm moved: the check cannot tell a move that small from
   none, and the verb's own description tells the pilot so. A goal within 0.05 degrees of every
   reading sends nothing, and says the arm is already there.
5. **Refused before anything is sent** when a goal lies outside the travel the robot's own
   description gives, when any joint reads past that travel, since a joint the pilot did not
   name is held where it reads, when the readings are stale, when the driver says the arm
   cannot move, while a move is running or another `move_joints` is still watching its own, or
   when the move would need longer than 12 s at the ceiling, which the refusal says, so the
   pilot splits it. Each goal that goes is held to the description's own radians.
6. **Answered by a person on `rosarm:ws`.** A standing answer does not clear a move there, and
   the move is refused before its question is put:

   ```text
   move_joints is answered by a person on this robot, each time, while nobody is asked here (--yes,
   a flock's standing answer or the MCP server): run it from a terminal, with quackd run or quackd
   drive and without --yes, and answer its confirm yourself
   ```

   `quackd run --yes` runs the task with every `move_joints` refused in those words, and the pilot
   hears them; without `--yes` it asks at the terminal, and a person's yes, or a script's on stdin,
   clears the move. `quackd
   drive` asks the person at its keyboard, or in its page, for each move, and under `--yes` the move
   is refused the same way. An MCP session asks nobody, so with or without `--yes` the move is
   refused, and a task that needs one is run from a terminal instead. On `rosarm:mock` `--yes`
   answers as it answers every body, so a rehearsal can script.
7. **Timed from its start.** The bridge makes an action client for every goal and waits for the
   controller's server before it sends the goal on, and the first goal through a bridge that has
   just started also waits while that client loads the action's types, about two seconds for
   Jazzy's rosbridge 2.7.1 in its official image. So a move is timed from its first feedback, the
   controller's sign that it has started, and a goal that shows no start within 8 s is cancelled
   as one nobody answered.

## Stop, the watchdog, and quackd going quiet

- **`stop`** cancels the running goal and waits up to 2 s for it to end CANCELED, and up to 8 s
  when the goal has not started yet, since the bridge holds that cancel until it has the goal. On a
  JointTrajectoryController the arm then holds where it is; on MotoROS2 the motion is held and its
  queue cleared, the job goes on, and the goal ends CANCELED with -500300. With no goal running
  there is nothing to send, since every one of these controllers holds its last point. A stop
  while the goal still runs sends its cancel again, since a cancel the bridge lost is answered by
  nobody. When the socket is gone or no answer came, the stop says it was not delivered, and to
  use the hardware switch.
- **The heartbeat** fails, and the run ends with a stop, when the socket is down, when no new
  `/joint_states` has come for five of the stream's own periods, half a second at least, and when
  the driver stops the arm itself while quackd watches: a UR safety mode other than NORMAL or
  REDUCED or its program stopped, a KUKA CONTROL_STOPPED or ERROR, a MotoROS2 e-stop or error. An
  arm that cannot move when the run starts is a refused move, not a failed heartbeat, so the
  lookout can still say so.
- **A socket that drops is let go of for good.** roslibpy holds what it is handed while the
  socket is down and sends it once its factory has reconnected by itself, a goal among them.
  So no goal is handed over on a dropped socket, and the heartbeat that finds it down lets go of
  the client: quackd hands it nothing more, and a new run or session is what drives the arm
  again. It closes this client's socket alone, never roslibpy's event loop, which every client
  in the process shares, another robot's in a flock among them.
- **When quackd goes quiet in the middle of a move, the arm finishes that move and holds there.**
  rosbridge cancels nothing for a client that has gone, the controller's own command timeout is
  off by default and counts from the end of a trajectory, and MotoROS2 is read to watch only its
  own agent.
  So the bound is one move: at most 12 s, at the speed asked. The manifest says it rather than
  implying a deadman, and the suite shows it on all four fake arms. A run that connects while that
  move runs is refused until it has ended, on the three arms whose controller says so.
- **A goal can still wake a MotoMini.** quackd never calls `start_traj_mode`, and a goal that
  arrives while MotoROS2's energy saving function has turned the servos off makes MotoROS2 call it
  itself and turn them on. A person confirms every move first for this reason among others.

## Units and limits

- **Degrees to the pilot, radians on the wire.** What goes on the wire and comes off it is
  converted in the transport.
- **The travel is the robot's own**, each joint's lower and upper from the description the bridge
  serves, published to a tenth rounded inward. MotoROS2 publishes no limits, so on the MotoMini
  the description comes from the owner's `robot_state_publisher`.
- **A speed ceiling of quackd's own:** no joint goes faster than a tenth of its rated velocity,
  18 degrees a second on a UR5e joint rated at 180, at the fastest point of its move and not on
  average. The JointTrajectoryController plays two points at rest as a quintic, whose speed at
  the middle is 15/8 of its mean, and MotoROS2 as a cubic, whose is 3/2, so a move takes at least
  the longest, over its joints, of 15/8 of each joint's distance over a tenth of that joint's rated
  velocity. A `duration_s` shorter than that is stretched, and the result says so. The rated
  velocity is the description's, and never above the stock description's own, so a figure no arm
  has lifts nothing, and the transport keeps the same ceiling for any intent it is handed.
- **One arm cannot be checked.** Under the LBR-Stack's stock `open_loop: true`, in its position
  mode (its impedance modes turn it off), `/lbr/joint_states`
  reports the command, so arrival there proves the command arrived, and `report_state` says so.
- **NaN is a reading of nothing.** The iisy's velocities and efforts are NaN unless its RSI
  configuration maps them; quackd reads positions alone.

## Upstream API

Read on 2026-11-08: ros2_controllers on its jazzy branch at `bee185c`, with what humble does
differently at `65db736` in the notes; control_msgs on jazzy at `ab911bf`; action_msgs at
`56800da`; the Universal Robots driver at `44d40de` and its description at `3852128`, both on
jazzy; KUKA's drivers at `15fbb69` and descriptions at `5ddbc41`; the LBR-Stack at `d37b751`;
MotoROS2 0.2.1 at `41ac074`, its interfaces at `b91a194`, the MotoMini's description at
`a77f186`; industrial_msgs at `d547cdc`. JointState and JointTrajectory are read at rosbridge's
own pin of ros2/common_interfaces. On 2026-11-09, where an action's server publishes its goals'
statuses: rcl at `18d96c9` and rclcpp at `35b821a`, both on jazzy. Every ref is in
[`adapters/rosarm/src/quackd_rosarm/upstream_api.py`](../../../adapters/rosarm/src/quackd_rosarm/upstream_api.py),
with its line. Nothing has been run against a controller.

### From the binding (imported from `quackd_rosbridge.upstream_api` by name)

The robot's description is read where the base adapter's introspection reads it, under
`DESCRIPTION_PARAM`, `DESCRIPTION_TOPIC` and `DESCRIPTION_NAMES`, which the rosbridge page lists
with the rest of that adapter's refs ([docs/adapters/rosbridge/README.md](../rosbridge/README.md)).

| Name | Why this family needs it |
|---|---|
| `op=send_action_goal {id, action, action_type, args, feedback}` | the goal |
| `op=cancel_action_goal {id, action}` | the stop |
| `op=action_feedback {id, action, values}` | read as the goal having started, and nothing more |
| `op=action_result {id, action, values, status, result}` | how a goal ended |
| `rosbridge_library 2.0.0 has actions` | what the bridge must be |
| `send_action_goals_in_new_thread` | why quackd refuses a bridge that says false |
| `IncomingQueue` | the reader a goal blocks |
| `send_action_goals_in_new_thread defaults by branch` | why a Humble bridge needs the flag |
| `/rosbridge_websocket` | where the parameter is asked for |
| `actions_glob` | why a goal can go unanswered, and the verb has a deadline of its own |
| `No action server available` | a refusal the verb says |
| `Action goal was rejected` | the same, for a controller that rejected it |
| `result true carries the goal's own status` | why quackd reads the status and not the flag |
| `a cancel reaches a goal the bridge is still running` | how the stop is answered |
| `a client that goes cancels nothing` | why one move runs on when quackd goes quiet |
| `/rosapi/action_servers` | the check that the action is there |
| `rosapi_msgs/srv/GetActionServers` | its shape |
| `/rosapi/get_param` | the parameter and the description |
| `rosapi_msgs/srv/GetParam` | its shape |
| `node:parameter` | how the bridge's own parameter is named |
| `get_param answers with JSON` | how its answer is read |
| `std_msgs/msg/String` | the latched description |
| `roslibpy.ActionClient(ros, name, action_type, reconnect_on_close=True)` | what sends the goal |
| `ActionClient.send_goal(goal, resultback, feedback, errback)` | returns at once, answers on roslibpy's thread |
| `ActionClient.cancel_goal(goal_id)` | the cancel |
| `roslibpy.Goal(values)` | the goal's wrapper |
| `roslibpy.GoalStatus` | the status the result carries |
| `resultback({status, values}) and errback({status, values})` | how a result and a refusal arrive |
| `self.once("ready", callback)` | a goal handed over while the socket is down would go out when roslibpy reconnects by itself, so no goal is sent on a dropped socket, and the heartbeat lets go of the client for good |
| `reactor.callFromThread(reactor.stop)` | why the transport closes its own socket and never calls `terminate()`, which would end every roslibpy client's socket in the process, another robot's in a flock among them |
| `a subscription is transient_local when every publisher on the topic is` | how the status a controller latched reaches quackd at connect |

### VERIFIED (read from source at the pins)

| Name | Note |
|---|---|
| `control_msgs/action/FollowJointTrajectory` | the goal is a trajectory and its tolerances; the result an `error_code` and an `error_string` |
| `trajectory_msgs/msg/JointTrajectory` | `{header, joint_names, points}` |
| `trajectory_msgs/msg/JointTrajectoryPoint` | positions in radians, velocities, accelerations, effort, `time_from_start` |
| `sensor_msgs/msg/JointState` | each joint known by its name, a revolute joint's position in radians |
| `action_msgs/msg/GoalStatus` | 0 UNKNOWN to 6 ABORTED |
| `action_msgs/msg/GoalStatusArray` | a status for each goal the server keeps, with when it was accepted |
| `<action>/_action/status` | where an action's server publishes its goals' statuses, reliable and transient local, so a late subscriber is handed the last array |
| `publish status since a goal's state has changed` | rclcpp_action's server, the controller's, publishes on every change; a goal ACCEPTED, EXECUTING or CANCELING has not ended |
| `<controller>/follow_joint_trajectory` | the controller's action |
| `Can't accept new action goals. Controller is not running.` | an inactive controller rejects every goal |
| `allow_partial_joints_goal` | false by default: a goal names every joint |
| `allow_nonzero_velocity_at_trajectory_end` | false on jazzy, true on humble |
| `a header stamp of zero starts the trajectory now` | the stamp quackd sends |
| `time_from_start strictly increasing` | the controller's check |
| `do quintic interpolation` | between two points that carry velocities and accelerations, as quackd's goal's do: at rest at both ends, its speed at the middle is 15/8 of its mean, which is the speed the ceiling holds |
| `a cancel holds the arm where it is` | unless `decelerate_on_cancel`, false by default |
| `a new goal ends the running one` | ABORTED on jazzy, CANCELED on humble |
| `Current goal cancelled during deactivate transition.` | a goal running when its controller stops ends ABORTED |
| `Goal successfully reached!` | SUCCEEDED once the last point's time has passed |
| `send feedback` | a running goal's feedback, which quackd reads as the controller having started it |
| `a tolerance of 0 is not checked` | and every tolerance defaults to 0 |
| `constraints.goal_time` | 0 by default: a goal outside its tolerance waits for ever |
| `cmd_timeout` | 0 by default: nothing ends a goal when its client goes quiet |
| `/scaled_joint_trajectory_controller/follow_joint_trajectory` | the UR's initial controller |
| `shoulder_pan_joint, shoulder_lift_joint, elbow_joint, wrist_1_joint, wrist_2_joint, wrist_3_joint` | the UR's joints, behind `tf_prefix` |
| `goal 0.1 rad, trajectory 0.2 rad` | the UR's tolerances, with `goal_time` 0 |
| `/io_and_status_controller/robot_program_running, /io_and_status_controller/safety_mode` | latched, and sent only on a change |
| `ur_dashboard_msgs/msg/SafetyMode` | 1 NORMAL, 2 REDUCED, 3 PROTECTIVE_STOP among thirteen |
| `the External Control program playing` | what a UR needs before a goal |
| `the UR5e's joint limits` | 360 degrees each way but the elbow, 180 degrees a second |
| `joint_1, joint_2, joint_3, joint_4, joint_5, joint_6` | the iisy's joints |
| `every controller spawned inactive` | until `robot_manager` is activated |
| `/event_broadcaster/hardware_event` | sent only on a change |
| `3 CONTROL_STARTED, 4 CONTROL_STOPPED, 6 ERROR` | the iisy's events |
| `ros2 lifecycle set robot_manager configure, then activate` | what an iisy needs before a goal |
| `velocity and effort NaN` | unless the RSI configuration maps them |
| `the LBR iisy 3 R760's joint limits` | from KUKA's own description |
| `lbr_A1, lbr_A2, lbr_A3, lbr_A4, lbr_A5, lbr_A6, lbr_A7` | the iiwa's joints, no constraints set |
| `/lbr` | the LBR-Stack's namespace, and the description's |
| `open_loop: true` | the readings are the commands |
| `LBRServer on the smartPAD` | what an iiwa needs before a goal |
| `the LBR iiwa 7 R800's joint limits` | a degree inside the robot's own |
| `follow_joint_trajectory` | MotoROS2's own action server |
| `joint_%d` | the MotoMini's joints |
| `every goal is accepted` | and an invalid one aborted with its code |
| `at least 2 points, at most 200` | MotoROS2's bounds |
| `every joint, with positions and velocities in every point` | matched by name |
| `time_from_start 0 only on the first point` | and never earlier than the one before |
| `The final point in a trajectory must specify a target velocity of '0'.` | MotoROS2's check |
| `the first point where the arm is commanded` | within `START_MAX_PULSE_DEVIATION` pulses of the command |
| `accCoef1` | MotoROS2 plays the points as a cubic in positions and velocities: at rest at both ends, its speed at the middle is 3/2 of its mean |
| `0.01 rad and 0.5 s` | MotoROS2's tolerances when a goal gives none |
| `Goal was cancelled by the user.` | CANCELED with -500300, a smooth stop |
| `Goal was aborted due to an alarm or error (check RobotStatus messages).` | ABORTED with -100303 |
| `Energy saving function is active. Re-enabling the robot.` | a goal can turn a MotoMini's servos back on |
| `motoros2_interfaces/msg/InitTrajEnum` | the codes of a goal MotoROS2 will not run |
| `motoros2_interfaces/msg/MotionReadyEnum` | the codes of a goal sent while motion is not possible |
| `robot_status` | how MotoROS2 fills it |
| `start_traj_mode` | which a person calls, and quackd never does |
| `the MotoMini's joint limits` | from Yaskawa's own description |
| `industrial_msgs/msg/RobotStatus` | e-stopped, drives powered, motion possible, in motion, in error |
| `industrial_msgs/msg/TriState` | 1 TRUE, 0 FALSE, -1 UNKNOWN |

### UNVERIFIED (our assumptions, and what quackd does about each)

| Name | What quackd does |
|---|---|
| `STOCK_NAMES` | every name in a body record is a stock bringup's default. The address sets the action, the readings topic and a joint prefix, and the connect checks the action and the joints against the bridge; a status topic nothing publishes is read as no status |
| `BRIDGE_NODE_NAME` | that the bridge is `/rosbridge_websocket` and rosapi reaches its parameters; an answer quackd cannot read is refused, and `?bridge_node=` names another |
| `STATUS_TYPES_LOADED` | that the bridge's machine has the driver's message package; without it no status is read, and `can_move` believes the driver |
| `UR_PROTECTIVE_STOP` | how a protective stop reaches the controller is not settled; any safety mode but NORMAL and REDUCED is a stop |
| `IIWA_STATUS` | where FRI's session state is published is not settled; nothing is read of the iiwa's driver |
| `IIWA_READINGS` | that the iiwa runs the stock `open_loop: true`; `report_state` says its readings are the commands |
| `MOTO_FIRST_POINT` | that a MotoMini at rest reads close enough to its command for a first point taken from the reading; one that does not is aborted with its reason |
| `MOTO_SINGLE_GOAL` | that MotoROS2 answers no second goal while one runs; quackd never sends one |
| `ACTION_STATUS_HEARD` | that the bridge hands quackd the status a controller latched within a second of subscribing; nothing heard is read as no goal running, since a controller that has had no goal publishes none |
| `MOTO_NO_ACTION_STATUS` | that MotoROS2 publishes no goal status, as rclc under it publishes none; on a MotoMini the connect hears nothing and refuses nothing |
| `ONE_MOVE_WHEN_QUIET` | that an arm whose client goes quiet finishes its move and holds; every move fits in 12 s, and the manifest claims no deadman |
| `FAKE_FROM_READING` | that the fake behaves as these sources read; it has met no bridge, no controller and no arm |

## Status

`rosarm:mock` is rehearsed. `tests/test_rosarm_tasks.py` runs the lookout and a slow wave on all
four bodies through the agent loop with the scripted pilot, a stop from beside a move, a heartbeat
that fails in the middle of one, a protective stop, a KUKA control stop and a MotoROS2 alarm mid
move, and a run that ends with a move in flight, whose teardown cancels it and parks nothing.
`tests/test_rosarm_fake.py` holds the fake to the sources, so that it does not quietly become
kinder than a controller, and `tests/test_rosarm_transport.py` holds the refusals at connect,
the goal's shape and the ways a result is read.

`tests/test_rosarm_contract.py` holds the transport to the real thing instead, in a job of its own
in CI, `rosarm contract` (`.github/workflows/rosarm-contract.yml`), in the official `ros:jazzy` and
`ros:humble` images: rosbridge_server and rosapi as each distribution releases them, roslibpy as
`uv.lock` pins it, a `robot_state_publisher` serving a description built from each arm's body
record, and a stand-in for the arm's driver in rclpy (`tests/rosarm_contract/arm.py`), whose action
server and its status are ROS 2's own. On both distributions, with each arm's names on the stand-in,
it shows every check at connect passing, a confirmed move arriving and a stop answered CANCELED, the
stock launch file's goal thread refused on Humble and taken on Jazzy, a cancel that lands only after
the move on a bridge without it, a goal left running refusing the next connect until it ends,
readings that stop failing the heartbeat, and a rejected goal read as refused. It is what found that
a bridge that has just started holds its first goal back about two seconds, which a move is now
timed around. It runs when the arm's code, the binding under it, `uv.lock` or the job itself
changes, and on demand, and anywhere with Docker, from the repository's root:

```bash
docker run --rm -v "$PWD":/repo:ro -w /repo ros:jazzy bash tests/rosarm_contract/run.sh
```

In Git Bash on Windows, put `MSYS_NO_PATHCONV=1` in front of it.

The stand-in is no controller, so what a JointTrajectoryController or MotoROS2 does with a goal is
still the fake's reading of it.

`rosarm:ws` is described: refused for every arm until somebody names a date for its first
hardware run, which nobody has. Three things are owed, in this order:

1. **A job against a real controller.** The same job with a stock `joint_trajectory_controller` on
   ros2_control's mock hardware in place of the stand-in, which would run what the fake says of
   the controller itself and catch where that reading is wrong.
2. **An owner and a date** for one arm, which makes that body bound and buys a hardware checklist
   for its first run.
3. **That run**, at a cell, with the pendant in a person's hand.
