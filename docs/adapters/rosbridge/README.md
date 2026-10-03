# rosbridge (a wheeled base over ROS 2)

The first robot in quackd that is not a specific product: any wheeled base that takes a
`geometry_msgs/msg/Twist` and is reachable through `rosbridge_server`. quackd talks to it
with [roslibpy](https://github.com/gramaziokohler/roslibpy) over a WebSocket. The manifest
is small and honest: one intent (`twist`), odometry, optionally a compressed image topic,
and therefore `move`, `stop`, `report_state`, plus `observe`, `go_to`, `search_scan` and
`approach_and` only when a camera topic is given. No `say`, no `gaze`, no `kick`. It has one
verb of its own, `introspect`, which asks the bridge what the body under it actually is. The
`ws` backend has **never been run against a bridge by us**.

```bash
uvx --from "quackd[rosbridge]" quackd list-verbs --robot rosbridge:mock
uvx --from "quackd[rosbridge]" quackd run patrol-and-quack --robot rosbridge:mock --llm fake   # exit 1: requires quack, but base-01 (rosbridge-base) does not provide it
uv pip install "quackd[rosbridge]"
quackd doctor --robot rosbridge:ws --address "ws://robot.local:9090?cmd_vel=/cmd_vel&odom=/odom&image=/camera/image/compressed"
```

The extra is two packages: `quackd-rosbridge`, quackd's own adapter, and roslibpy, which only
the `ws` backend imports. A bare `uv pip install quackd` carries no robot at all, so `mock`
needs the extra as much as `ws` does. `uv pip install quackd-rosbridge` is the adapter without
roslibpy, which is enough for `mock` and for reading the manifest.

The address carries everything: host, port, `ws` or `wss`, and the topics as query
parameters (`cmd_vel` and `odom` default to `/cmd_vel` and `/odom`; `image` is optional
and turns the camera verbs on). Two more name where the robot's own description lives:
`urdf_param` defaults to `/robot_state_publisher:robot_description` and `urdf_topic` to
`/robot_description`, and either can be set to `off`.

## Backends

| `--robot` | Status | What it is |
|---|---|---|
| `rosbridge:mock` | ✅ | a planar kinematic integrator with the simulator's deadman semantics (a Twist holds for half a second, then the base coasts to zero), integrated odometry, a synthetic camera with an orange disc at a fixed spot |
| `rosbridge:ws` | 🧪 | roslibpy 2.x to a rosbridge server (extra `quackd[rosbridge]`); every roslibpy, rosbridge protocol and message name VERIFIED against pinned commits, never run against a bridge |

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
quackd's caution, not the base's capability. A manifest can lower them, but not raise them past
`move`'s own schema bounds (±0.3 m/s, ±0.2 m/s, ±1.5 rad/s), which reject a larger request.

## What the bridge tells us about the body

`rosbridge` is a name for a transport. It says nothing about what the robot on the other
side weighs, how far it reaches or what it can climb, so this is the one adapter in quackd
whose datasheet is not a constant: it is whatever the bridge answered.

At `connect()`, and again whenever the pilot calls `introspect`, quackd asks for three
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
a gripper can hold, and quackd on this adapter commands a velocity and nothing else. Joints
named like a gripper are noted and claimed as nothing. A pilot reading a datasheet full of
"not published" is told to answer `uncertain` wherever a task turns on one of them, and a
`feasible` that names one is refused, which is the point.

Two caps bound what comes back. A description over 8 MiB is refused unparsed, so mass and
joint count stay unknown with `the description is over 8192 KiB` as the reason, and only the
first 24 moving joints reach the notes, the rest counted as `and N more joints not listed
here`. Neither is settable.

If nothing answers, quackd connects anyway and the datasheet says `nothing discovered on the
bridge`, with the reason. A bridge launched without the `rosapi` node is the usual cause,
and both description sources can be turned off in the address (`?urdf_param=off`,
`?urdf_topic=off`). That skips the two description reads and not the topic list, which is
asked for first and under the same deadline, so a bridge with no `rosapi` still costs it.

`introspect` re-reads all of it and returns the topic list and the description, so a pilot
can look again after a robot has finished booting. It refreshes the manifest's datasheet in
place, but the prompt built at connect keeps the old text: read the result.

## Safety

There is no deadman anywhere in this stack that we verified: neither rosbridge nor a
base's driver. quackd re-sends the Twist at 10 Hz while a verb runs and publishes a zero
Twist on `stop`, on `close()`, and when the heartbeat fails, and that is the only stop
authority. The manifest says exactly that (`native: none`, `deadman: false`). A base
whose driver does implement a command timeout is safer than this page assumes.

## Upstream API

Three upstreams, each pinned and read on 2026-09-02: roslibpy at `f5793db` (2.1.0 on
PyPI), rosbridge_suite at `aa9a7a3` (the `ros2` branch), and ros2/common_interfaces at
`d54aa9b` (`rolling`). Two more were pinned and read on 2026-09-13, for introspection:
ros/urdfdom at `bfcf29f` (what a URDF says) and ros/robot_state_publisher at `5526c76`
(where the URDF usually is).

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
| `compression: png or none` | quackd subscribes with `none` |
| `roslibpy.Message(values)` | a `UserDict` |
| `JSON text frames` | `json.loads` on every frame, keyed by `op` |
| `op=advertise {id, topic, type, latch, queue_size}` | |
| `op=publish {id, topic, msg, latch}` | |
| `op=subscribe {id, topic, type, compression, throttle_rate, queue_length}` | |
| `uint8[] fields arrive base64-encoded` | a `CompressedImage`'s `data` is one |
| `pkg/Type and pkg/msg/Type both resolve` | quackd sends the ROS 2 three-part form |
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
| `get_param answers with JSON` | `return dumps(value)`, so a description arrives quoted; quackd decodes it and keeps the raw text when that fails |
| `Ros.get_param(name, callback=None, errback=None)` | what roslibpy offers; quackd calls the service itself, because this one decodes inside roslibpy's own thread |
| `subscribe carries an optional qos` | protocol 4.3.4; roslibpy 2.1.0 never sends one |
| `a subscription is transient_local when every publisher on the topic is` | which is how a subscription made now still sees a description published at startup |
| `std_msgs/msg/String` | `{data}`; what a `robot_description` topic carries |
| `robot_state_publisher` | the node name, and it declares a `robot_description` parameter |
| `/robot_description` | published `std_msgs::msg::String` with `QoS(1).transient_local()` |
| `/robot_state_publisher:robot_description` | that parameter in the form above; the default of `?urdf_param=` |
| `link/inertial/mass@value` | required inside an `inertial`, and the `inertial` itself is optional per link |
| `joint@type in planar, floating, revolute, continuous, prismatic, fixed` | the six strings the parser accepts |
| `joint/limit@lower,upper,effort,velocity` | `lower` and `upper` are optional |
| `joint@name, joint/parent@link, joint/child@link` | the tree |

### UNVERIFIED (our assumptions, and what quackd does about each)

| Name | What quackd does |
|---|---|
| `NO_DEADMAN` | re-sends at 10 Hz, zeroes on stop; the manifest says `native: none` |
| `TOPIC_NAMES` | every topic is set in the address query; the defaults are conventions |
| `TWIST_UNITS` | m/s and rad/s in the base frame, angular z positive to the left |
| `IMAGE_FORMAT` | jpeg or png decoded with PIL, channels swapped when the format says `bgr8`, anything else refused |
| `ODOM_YAW` | yaw from the quaternion's z and w, a planar base assumed |
| `THREAD_SAFETY` | only the latest odometry and image are kept, under a lock; nothing calls back into the event loop from roslibpy's thread |
| `ROS1_BRIDGE` | a ROS 1 rosbridge should accept the three-part type strings; not tried |
| `DESCRIPTION_NAMES` | that the description is on `/robot_state_publisher`, un-namespaced, as a parameter; both names are in the address and either can be `off` |
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
