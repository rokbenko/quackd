# rosbridge (a wheeled base over ROS 2)

The first robot in Roboglot that is not a specific product: any wheeled base that takes a
`geometry_msgs/msg/Twist` and is reachable through `rosbridge_server`. Roboglot talks to it
with [roslibpy](https://github.com/gramaziokohler/roslibpy) over a WebSocket. The manifest
is small and honest: one intent (`twist`), odometry, optionally a compressed image topic,
and therefore `move`, `stop`, `report_state`, the place verbs `map`, `mark_place`,
`forget_place` and `go_to_place` on its `/odom`, plus `observe`, `go_to`, `search_scan` and
`approach_and` only when a camera topic is given. No `say`, no `gaze`, no `kick`. It has one
verb of its own, `introspect`, which asks the bridge what the body under it actually is. The
`ws` backend has **never been run against a bridge by us**.

```bash
uvx --from "roboglot[rosbridge]" roboglot list-verbs --robot rosbridge:mock
uvx --from "roboglot[rosbridge]" roboglot run patrol-and-quack --robot rosbridge:mock --llm fake   # exit 1: requires quack, but base-01 (rosbridge-base) does not provide it
uv pip install "roboglot[rosbridge]"
roboglot doctor --robot rosbridge:ws --address "ws://robot.local:9090?cmd_vel=/cmd_vel&odom=/odom&image=/camera/image/compressed"
```

The extra is two packages: `roboglot-rosbridge`, Roboglot's own adapter, and roslibpy, which only
the `ws` backend imports. A bare `uv pip install roboglot` carries no robot at all, so `mock`
needs the extra as much as `ws` does. `uv pip install roboglot-rosbridge` is the adapter without
roslibpy, which is enough for `mock` and for reading the manifest.

The address carries everything: host, port, `ws` or `wss`, and the topics as query
parameters (`cmd_vel` and `odom` default to `/cmd_vel` and `/odom`; `image` is optional
and turns the camera verbs on). Two more name where the robot's own description lives:
`urdf_param` defaults to `/robot_state_publisher:robot_description` and `urdf_topic` to
`/robot_description`, and either can be set to `off`.

## Backends

| `--robot` | Status | What it is |
|---|---|---|
| `rosbridge:mock` | ✅ | a planar kinematic integrator with a deadman (a Twist holds for half a second, then the base coasts to zero), integrated odometry, a synthetic camera with an orange disc at a fixed spot |
| `rosbridge:ws` | 🧪 | roslibpy 2.x to a rosbridge server (extra `roboglot[rosbridge]`); every roslibpy, rosbridge protocol and message name VERIFIED against pinned commits, never run against a bridge |

## The manifest

```json
{
  "manifest": 1, "id": "base-01", "vendor": "ros", "model": "rosbridge-base",
  "embodiment": "wheeled", "mobility": "wheeled",
  "intents": ["twist"], "sensors": ["odometry", "camera"],
  "verbs": ["observe", "report_state", "stop", "move", "introspect", "go_to", "search_scan", "approach_and"],
  "preconditions": {},
  "safety_authority": {"native": "none", "deadman": false, "heartbeat_hz": 2.0},
  "frame": {"reference": "base", "note": "Twist in the base frame; odometry in its odom frame"},
  "limits": {"max_vx": 0.3, "max_vy": 0.0, "max_wz": 1.0},
  "extras": {"ros": "2", "cmd_vel": "/cmd_vel", "odom": "/odom", "image": null}
}
```

Every verb but `introspect` is a core verb: the adapter mostly says what it has, and adds
the one thing that asks. The `blurb` and the `datasheet` are left out above: the
blurb is one fixed line on either backend, and the sheet is the thing that is not a constant
here. On `mock` it reads 11 kg over three link inertials and two moving joints, read from the
canned URDF the mock serves.
The `limits` are what `move`, `go_to` and the turn used by `search_scan` clamp to; they are
Roboglot's caution, not the base's capability. A manifest can lower them, but not raise them past
`move`'s own schema bounds (±0.3 m/s, ±0.2 m/s, ±1.5 rad/s), which reject a larger request.

## What the bridge tells us about the body

`rosbridge` is a name for a transport. It says nothing about what the robot on the other
side weighs, how far it reaches or what it can climb, so this is the one adapter in Roboglot
whose datasheet is not a constant: it is whatever the bridge answered.

At `connect()`, and again whenever the pilot calls `introspect`, Roboglot asks for three
things, under one deadline of `timeout_s` so a silent bridge costs that much and no more:

1. the topic list, from `/rosapi/topics`;
2. the robot's description, from the `robot_description` parameter through
   `/rosapi/get_param`, addressed as `node:parameter` because that is how the ROS 2 rosapi
   splits the name;
3. failing that, the `/robot_description` topic, which `robot_state_publisher` publishes
   transient-local, and which a rosbridge subscription inherits when every publisher on the
   topic has it.

Two things come out of a URDF and go into the datasheet, both tagged `official` and sourced
to the file they were read from, because it is the robot's own:

| From the URDF | Into the datasheet |
|---|---|
| the sum of every `link/inertial/mass@value` | `mass_kg`, with a note saying how many links had an inertial at all |
| the joints whose type is not `fixed` | `dof`, with every moving joint and its limits listed in the notes |

Everything else stays unknown, **payload above all**: a description says nothing about what
a gripper can hold, and Roboglot on this adapter commands a velocity and nothing else. Joints
named like a gripper are noted and claimed as nothing. A pilot reading a datasheet full of
"not published" is told to answer `uncertain` wherever a task turns on one of them, and a
`feasible` that names one is refused, which is the point.

Two caps bound what comes back. A description over 8 MiB is refused unparsed, so mass and
joint count stay unknown with `the description is over 8192 KiB` as the reason, and only the
first 24 moving joints reach the notes, the rest counted as `and N more joints not listed
here`. Neither is settable.

If nothing answers, Roboglot connects anyway and the datasheet says `nothing discovered on the
bridge`, with the reason. A bridge launched without the `rosapi` node is the usual cause,
and both description sources can be turned off in the address (`?urdf_param=off`,
`?urdf_topic=off`). That skips the two description reads and not the topic list, which is
asked for first and under the same deadline, so a bridge with no `rosapi` still costs it.

`introspect` re-reads all of it and returns the topic list and the description, so a pilot
can look again after a robot has finished booting. It refreshes the manifest's datasheet in
place, but the prompt built at connect keeps the old text: read the result.

## Safety

There is no deadman anywhere in this stack that we verified: neither rosbridge nor a
base's driver. Roboglot re-sends the Twist at 10 Hz while a verb runs and publishes a zero
Twist on `stop`, on `close()`, and when the heartbeat fails, and that is the only stop
authority. The manifest says exactly that (`native: none`, `deadman: false`). A base
whose driver does implement a command timeout is safer than this page assumes.

**What a stop can know.** roslibpy raises nothing for a publish with the socket down: it holds
the message until the socket is back and sends it then, with nobody watching, or drops it with
a client that was asked to close. So the link is read before every publish. With the socket
down, `move` is refused (`is not connected, so no Twist is sent`), nothing is handed to the
queue, and a stop says it was not sent: `stop could not be delivered, so the body may still be
moving: rosbridge at ws://robot.local:9090 is not connected, so the zero Twist was not sent,
and the base, which has no deadman, keeps the last Twist that reached it`. Every core verb that
ends on a stop says the same of its own, and so does an abort, and a zero that `close()` could
not send is said by the run after its close (`close_note`). A socket that reads up is no proof
that a message arrived: a link that dropped between the read and the publish is not seen, and
roslibpy's own reconnect, which would send what it held, is not reproduced by the tests here.

**What the heartbeat can know.** A link that dies without a close, as one over Wi-Fi does,
leaves the socket reading up until TCP gives up, which can be minutes. So the heartbeat fails
on more than a closed socket, and on nothing less than a link that has given no sign of life
for two beats in a row: one beat without one is a slow link. A sign of life is any message on a
topic it subscribes, `/odom` or the camera's, or any answer from the bridge, a failed one
included, as a bridge gives while its `rosapi` node restarts. A stream is judged by its pace
now, the slowest of its last five gaps and no slower than half a hertz, and is given five of
that pace, a second at least and ten at most: a pause the link came back from, a Wi-Fi hiccup or
a stalled process, widens nothing once five fresh gaps have come. Messages that a long gap held
and that come back to back once it ends, a hundredth of a second apart or less, are not its
pace, and set nothing: the gap that held them is the pace until the stream's own gaps come
again. A stream is not judged before
its pace is known, so a base whose `/odom` comes once every two seconds is not quiet between its
first messages. A beat past that window is silent whether the bridge has `rosapi` or not. It
asks the bridge for its topic list in the background, and the answer is a sign of life when it
comes, but no beat waits for it, so a link that is gone is noticed as soon with `rosapi` as
without. A stream that stopped over a link that still answers, a base whose `/odom` went quiet
while its bridge runs on among them (see the stop, below), keeps its run only while the bridge
answers each ask before the next beat, within half a second. An answer later than that leaves the
next beat silent too, and the run ends at that second silent beat, within the window and two
beats of the last sign of life: 2 s at 10 Hz, 11 s at the slowest pace, as on a link that is
gone. Where no stream
vouches, as on a base that publishes no `/odom`, or publishes it on a topic the address does not
name, the heartbeat asks for the topic list on a bridge that answered at connect, once the last
sign of life is a second old, and counts a beat silent only once that request has gone two seconds
unanswered, and then asks again, so one lost answer is not a link gone. Two silent beats in a
row fail it, in words such as `reads connected, but nothing has come over it for 2.0 s, on
/odom, longer than the 1.0 s its streams are given, 2 beats in a row, so the link may be gone`,
or, with no stream, `reads connected, but the bridge did not answer a request for its topic list
(/rosapi/topics) within 2 s, 2 beats in a row, so the link may be gone`. On a bridge that gave no
answer at connect, and with no stream whose pace is known, nothing watches the link but the
socket's own state.

**What a stop can tell of a quiet link.** A window cannot tell a stop sent just after the link
died from one sent just before it, so a stop listens. Its zero Twist goes out at once; then, on a
link whose streams have a pace, it waits for a sign of life for two of that pace, and a second
at least: a message, or the answer to a request for the topic list it sends where the bridge
answers. The second is the window's own floor, since a pace is measured from arrivals, and
messages that came together, a driver flushing its queue, measure one far shorter than the
stream's. Something comes, and the zero is taken to have landed. Nothing comes, and the stop
says it went out and is not known to have arrived: `stop went out but is not known to have
reached the body, so the body may still be moving: rosbridge at ws://robot.local:9090 reads
connected, but nothing has come over it, on /odom, in the 1.0 s after the zero Twist went out,
so the zero Twist is not known to have reached the base, which has no deadman and keeps its last
Twist if it did not`.

Where `/odom` had a pace and has gone quiet past its window, only `/odom` vouches for the base.
A base's driver can die, or the cable to its motor board come out, while rosbridge and `rosapi`
run on, and the bridge's answers say nothing of the base. So such a stop listens for `/odom`
alone, asks the bridge for its topic list with a request of its own, whatever else is out, and
with only the bridge heard says what it heard and when. Over one socket only the answer to that
request, sent after the zero, shows the zero reached the bridge: `stop went out but is not known
to have reached the body, so the body may still be moving: rosbridge at ws://robot.local:9090
answered a request sent after the zero Twist, 0.0 s after it went out, so the zero reached the
bridge, but /odom has brought nothing for 3.0 s, longer than the 1.0 s its pace gives it, so the
base is not known to have taken the zero Twist, and it has no deadman and keeps its last Twist if
it did not`. A camera's image, which travels the other way, is said as what it is, `which shows
the link is up and not that the zero reached the bridge`, and so is an answer to a request other
than the stop's own, a beat's sent before the zero or during the listen, `which shows the link
is up`. A base that publishes `/odom` only while it moves says this of a stop sent
after it has stood still past that window. A base that never had `/odom`, a camera's base among
them, is vouched for by its link, as before.

Listening is what a stop costs here. On a live link it ends with the next message or the answer,
and at worst, on a stream at the slowest pace taken, a stop's answer comes four seconds after its
zero, inside the five the `stop` verb is given and the executor waits for a stop it owes. A base
with no stream has nothing to listen to, and its stop says what the socket says. The close does
not listen: it lets go of the client at once, and where the streams had gone quiet past their
window and nothing had answered, it says its zero is not known to have arrived, in `close_note`,
which the run says after the close.

The worst cases follow from the constants: the heartbeat beats every half second (B), the
floor is a second, a window is five of a stream's pace, two silent beats fail it, the slowest
pace taken is two seconds, a request is given two seconds before a beat counts it unanswered,
and a stop listens two of its stream's pace, a second at least.

| Kind of base | Longest a dead link goes unnoticed | What a stop says meanwhile |
|---|---|---|
| `/odom` at 10 Hz, with `rosapi` or without | a second, then 2B: 2 s | `went out but is not known to have reached the body`, after listening 1 s |
| `/odom` at half a hertz, or slower | 5 × 2 s, then 2B: 11 s, the most for any stream | the same, after listening 4 s |
| `/odom` at 10 Hz after a pause it came back from, of 3 s or 30 s | 2 s again once five fresh gaps have come, and 11 s at most before then | the same, after listening 1 s, and 4 s at most before then |
| `/odom` at 10 Hz gone quiet, while the bridge answers or a camera still sends: the base's driver gone | never while a camera still sends or the bridge answers each ask within a beat, half a second; a bridge slower than that ends the run as a dead link does, a second, then 2B: 2 s after `/odom` went quiet | `went out but is not known to have reached the body`, what the bridge sent and when, `but /odom has brought nothing`, after listening 1 s for `/odom` |
| no stream, a bridge that answered at connect | a second, B, then twice 2 s and B: 6.5 s | `stopped (velocity zeroed)`, while the socket reads up |
| no stream, a bridge that did not answer at connect | never: only the socket's own state | `stopped (velocity zeroed)`, while the socket reads up |

**The topic it drives.** The command topic is held against the topic list the bridge gives,
each time one is read: at connect, before anything is published, whenever `introspect` reads
the bridge again, and when a move would be refused. A topic the list does not name, or names as
another type, is not driven: `move` is refused with what the bridge listed, for instance `the
bridge lists /cmd_vel as geometry_msgs/msg/TwistStamped, and Roboglot publishes only
geometry_msgs/msg/Twist, so it sends no Twist there (the Twist topics it lists: none)`, and the
datasheet says the same. A base driver that comes up after the connect is driven once the
bridge lists its topic. roslibpy advertises the topic as a Twist on the first publish, so after
Roboglot has sent on it the list shows Roboglot's own, and a topic that went is seen as gone
only while Roboglot has sent nothing on it. Where the list names more than one Twist topic, the
datasheet says which one is driven: `Roboglot drives /cmd_vel, the topic the address names; the
bridge lists 1 more Twist topic it does not drive: /cmd_vel_nav`. A bridge with no `rosapi`
gives no list, and the topic is driven as before. Being listed is no proof that a base
subscribes.

**A camera that goes quiet.** Each image is dated by its arrival, as nothing on the wire says
how long it was on the way, and one older than four of the stream's own periods, two seconds at
least, is not handed out, the rule the Microduck keeps for the same hazard. `observe`, `go_to`
and `search_scan` say why: `the newest image on /cam/compressed is 3.1 s old, older than the
2.0 s Roboglot steers on`.

`go_to_place` walks on `/odom`, every message taken into the reading as it arrives, so the
stream's own period sizes the walk's windows however seldom Roboglot reads it. A message is dated
by its header stamp, or by its arrival while the stack has stamped none; once one is stamped, an
unstamped message is another clock's and no reading, and the same stamp again is the same
reading. With no deadman, a twist left standing would drive the base on, so a walk whose reading
stops moving on sends a zero Twist within a reading's freshness, and ends once no new reading
has come for five of the stream's periods, a second at least. Its frame lasts one connection,
as `/odom`'s origin is wherever the stack started.

## Upstream API

Three upstreams, each pinned and read on 2026-09-02: roslibpy at `f5793db` (2.1.0 on
PyPI), rosbridge_suite at `aa9a7a3` (the `ros2` branch), and ros2/common_interfaces at
`d54aa9b` (`rolling`). Two more were pinned and read on 2026-09-13, for introspection:
ros/urdfdom at `bfcf29f` (what a URDF says) and ros/robot_state_publisher at `5526c76`
(where the URDF usually is). On 2026-11-08 the same two pins were read again: fourteen refs
that pointed at the wrong lines of their files now point at the right ones, and the action refs
at the end of the table were added for the industrial arm family, which sends goals over this
binding and imports them by name ([docs/adapters/rosarm/README.md](../rosarm/README.md)). This
adapter sends no goal.

### VERIFIED (read from source at the pins)

| Name | Note |
|---|---|
| `roslibpy` | 2.1.0 at the pin and on PyPI, Python 3.9 or newer |
| `roslibpy.Ros(host, port=None, is_secure=False, headers=None, transport=None)` | the constructor already calls `connect()` |
| `Ros.run(timeout)` | starts the non-blocking loop and waits until connected |
| `Ros.close(timeout)` | |
| `Ros.terminate()` | closes if connected, then stops the loop |
| `Ros.is_connected` | the heartbeat |
| `Ros.on_ready(callback, run_in_thread=True)` | |
| `Ros.get_topics(callback, errback)` | |
| `Ros.get_topic_type(topic, callback, errback)` | |
| `roslibpy.Topic(ros, name, message_type, compression=None, latch=False, throttle_rate=0, queue_size=100, queue_length=0, reconnect_on_close=True)` | |
| `Topic.publish(message)` | advertises on first use and sends `dict(message)`, so a plain dict works |
| `Topic.subscribe(callback)` | `callback(message: dict)` |
| `Topic.unsubscribe()` | |
| `Topic.advertise()` | |
| `Topic.unadvertise()` | |
| `compression: png or none` | Roboglot subscribes with `none` |
| `roslibpy.Message(values)` | a `UserDict` |
| `JSON text frames` | `json.loads` on every frame, keyed by `op` |
| `op=advertise {id, topic, type, latch, queue_size}` | |
| `op=publish {id, topic, msg, latch}` | |
| `op=subscribe {id, topic, type, compression, throttle_rate, queue_length}` | |
| `uint8[] fields arrive base64-encoded` | a `CompressedImage`'s `data` is one |
| `pkg/Type and pkg/msg/Type both resolve` | Roboglot sends the ROS 2 three-part form |
| `geometry_msgs/msg/Twist` | `{linear: Vector3, angular: Vector3}` |
| `geometry_msgs/msg/Vector3` | `{x, y, z}` |
| `sensor_msgs/msg/CompressedImage` | `{header, format, data: uint8[]}` |
| `nav_msgs/msg/Odometry` | `{header, child_frame_id, pose: PoseWithCovariance, twist: TwistWithCovariance}` |
| `geometry_msgs/msg/Pose` | `{position: Point, orientation: Quaternion}` |
| `geometry_msgs/msg/Quaternion` | `{x, y, z, w}` |
| `roslibpy.Service(ros, name, service_type, reconnect_on_close=True)` | the client side of a service call |
| `Service.call(request, callback=None, errback=None, timeout=None)` | with no callback it blocks until the answer or the deadline, and raises `ServiceException` when the answer carries one |
| `roslibpy.ServiceRequest(values)` | a UserDict, like `Message` |
| `op=call_service {id, service, args}` | protocol 4.4.3; the answer is `op=service_response {id, service, values, result}` |
| `rosapi` | the stock launch starts it beside the bridge, so its services are `/rosapi/...`; without it nothing here answers |
| `/rosapi/topics` | `create_service(Topics, "~/topics")`; the handler fills `response.topics` and `response.types` |
| `rosapi_msgs/srv/Topics` | no request fields; `{string[] topics, string[] types}` |
| `/rosapi/get_param` | `create_service(GetParam, "~/get_param")` |
| `rosapi_msgs/srv/GetParam` | `{string name, string default_value}` to `{string value, bool successful, string reason}` |
| `node:parameter` | the ROS 2 rosapi splits `request.name` on a colon, and makes the node name absolute |
| `get_param answers with JSON` | `return dumps(value)`, so a description arrives quoted; Roboglot decodes it and keeps the raw text when that fails |
| `Ros.get_param(name, callback=None, errback=None)` | what roslibpy offers; Roboglot calls the service itself, because this one decodes inside roslibpy's own thread |
| `subscribe carries an optional qos` | protocol 4.3.4; roslibpy 2.1.0 never sends one |
| `a subscription is transient_local when every publisher on the topic is` | which is how a subscription made now still sees a description published at startup |
| `std_msgs/msg/String` | `{data}`; what a `robot_description` topic carries |
| `robot_state_publisher` | the node name, and it declares a `robot_description` parameter, unless it is started with `use_robot_description_topic`, when it subscribes to the topic and declares none |
| `/robot_description` | published `std_msgs::msg::String` with `QoS(1).transient_local()` |
| `/robot_state_publisher:robot_description` | that parameter in the form above; the default of `?urdf_param=` |
| `link/inertial/mass@value` | required inside an `inertial`, and the `inertial` itself is optional per link |
| `joint@type in planar, floating, revolute, continuous, prismatic, fixed` | the six strings the parser accepts |
| `joint/limit@lower,upper,effort,velocity` | `lower` and `upper` are optional |
| `joint@name, joint/parent@link, joint/child@link` | the tree |
| `op=send_action_goal {id, action, action_type, args, feedback}` | protocol 4.5.3; no message says a goal was accepted |
| `op=cancel_action_goal {id, action}` | protocol 4.5.4; a goal that has completed cannot be cancelled |
| `op=action_feedback {id, action, values}` | protocol 4.5.5 |
| `op=action_result {id, action, values, status, result}` | protocol 4.5.6; `status` is action_msgs' integer, and `result: false` carries a string |
| `rosbridge_library 2.0.0 has actions` | since 2.0.0, 2024-10-08, on the humble and the jazzy branch too; a bridge from before it answers no goal |
| `send_action_goals_in_new_thread` | the capability's own default is false, and then a goal runs to its end on the thread that reads every message from its client, so a cancel waits behind it (rosbridge_suite issue #909) |
| `IncomingQueue` | one thread per client, taking its messages one at a time |
| `send_action_goals_in_new_thread defaults by branch` | true on `ros2` and on jazzy, false on humble, whose node warns at startup that goals will block the main thread |
| `/rosbridge_websocket` | the node's name, and it declares every protocol parameter as a read-only ROS parameter, so rosapi can be asked for one |
| `actions_glob` | a goal to an action it does not match is dropped with no `action_result` at all |
| `No action server available` | the answer after a second with no server |
| `Action goal was rejected` | a server's refusal, with status 0 and nothing of the server's reason |
| `result true carries the goal's own status` | SUCCEEDED, CANCELED and ABORTED all arrive as `result: true` |
| `a cancel reaches a goal the bridge is still running` | and the goal's own result answers it |
| `a client that goes cancels nothing` | a goal sent goes on to its end after its client's socket closes |
| `/rosapi/action_servers` | `create_service(GetActionServers, "~/action_servers")`, each name with an `_action/feedback` and an `_action/status` topic |
| `rosapi_msgs/srv/GetActionServers` | no request fields; `{string[] action_servers}` |
| `roslibpy.ActionClient(ros, name, action_type, reconnect_on_close=True)` | `reconnect_on_close` is stored and never read, so a goal does not outlive a reconnect |
| `ActionClient.send_goal(goal, resultback, feedback, errback)` | returns the goal's id at once and always asks for feedback |
| `ActionClient.cancel_goal(goal_id)` | its docstring says async cancelation is not yet supported on rosbridge |
| `roslibpy.Goal(values)` | a UserDict |
| `roslibpy.GoalStatus` | an Enum of action_msgs' statuses, 0 to 6 |
| `resultback({status, values}) and errback({status, values})` | a status outside the Enum, a feedback after its result and an `op: status` are each skipped with a log line |
| `self.once("ready", callback)` | a send handed over while the socket is down waits, and goes out when the client reconnects by itself: an action goal too, which is why the industrial arm lets go of a client whose socket dropped |
| `reactor.callFromThread(reactor.stop)` | `Ros.terminate()` stops the one reactor every roslibpy client in a process runs on, so it ends every client's socket: this adapter and the industrial arm each close their own socket instead, and leave the reactor to the rest |

### UNVERIFIED (our assumptions, and what Roboglot does about each)

| Name | What Roboglot does |
|---|---|
| `NO_DEADMAN` | re-sends at 10 Hz, zeroes on stop; the manifest says `native: none` |
| `TOPIC_NAMES` | every topic is set in the address query; the defaults are conventions |
| `TWIST_UNITS` | m/s and rad/s in the base frame, angular z positive to the left |
| `IMAGE_FORMAT` | jpeg or png decoded with PIL, channels swapped when the format says `bgr8`, anything else refused |
| `ODOM_YAW` | yaw from the quaternion's z and w, a planar base assumed |
| `THREAD_SAFETY` | only the latest odometry and image are kept, under a lock; nothing calls back into the event loop from roslibpy's thread |
| `ROS1_BRIDGE` | a ROS 1 rosbridge should accept the three-part type strings; not tried |
| `DESCRIPTION_NAMES` | that the description is on `/robot_state_publisher`, un-namespaced, as a parameter, which it is not on a node started with `use_robot_description_topic`; both names are in the address and either can be `off` |
| `URDF_MASS_IS_THE_BODY` | that the link inertials add up to what the robot weighs; the figure is tagged official because the file is the robot's own, and the note says how many links had one |
| `DOF_IS_NON_FIXED_JOINTS` | that every non-fixed joint is a degree of freedom, so wheels, casters and fingers all count |

## Status

`rosbridge:mock` drives, coasts on silence, turns, and reaches the ball through the
executor in the test suite, and serves a canned description so `introspect` and the
datasheet have something to read offline. `rosbridge:ws` is exercised with an injected fake
client, fake topics and fake services (verified names, message shapes, base64 images,
odometry, a description read from the parameter and from the topic, and a bridge that
answers neither). Nobody has run it against a bridge, and this page will say so until
someone has.
